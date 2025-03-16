"""Commands designed to spam the chat with various things."""

import datetime
from collections import defaultdict

import disnake
from disnake.ext import commands, tasks

from markovbot.lib import markov
from markovbot.lib.config import BotConfig
from markovbot.lib.custom_cog import CustomCog
from markovbot.lib.custom_command import slash_command_with_cooldown
from markovbot.lib.markov import MARKOV_MODEL, generate_text_from_markov_chain, update_markov_chain_for_model
from markovbot.lib.messages import send_message_to_channel


class Markov(CustomCog):  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """A collection of commands to spam the chat with."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        bot: commands.InteractionBot,
        attempts: int = 10,
    ) -> None:
        """Initialize the cog.

        Parameters
        ----------
        bot: commands.InteractionBot
            The bot object.
        attempts: int
            The number of attempts to generate a markov sentence.

        """
        super().__init__(bot)

        self.attempts = attempts
        self.messages = []
        self.markov_training_sample = {}
        self.cooldowns = defaultdict(
            lambda: {"count": 0, "last_interaction": datetime.datetime.now(tz=datetime.UTC)},
        )

        # If no markov model, don't start the loop.
        if MARKOV_MODEL:
            self.markov_chain_update_loop.start()  # pylint: disable=no-member

    @staticmethod
    async def send_markov_response(
        message: disnake.Message, seed_word: str, *, dont_tag_user: bool = False
    ) -> list[disnake.Message]:
        """Send a fallback response using the markov chain.

        Parameters
        ----------
        message : disnake.Message
            The message to respond to.
        seed_word : str
            The seed word for sentence generation
        dont_tag_user: bool
            Whether or not to tag the user or not, optional

        """
        return await send_message_to_channel(
            generate_text_from_markov_chain(MARKOV_MODEL, seed_word, 1),
            message,
            dont_tag_user=dont_tag_user,  # In a DM, we won't @ the user
        )

    async def is_on_cooldown(self, user_id: int) -> bool:
        """Check if the user is on cooldown."""
        user_data = self.cooldowns[user_id]
        await self.reset_cooldown_if_needed(user_data)
        if user_data["count"] < BotConfig.get_config("COOLDOWN_RATE"):
            return False
        return datetime.datetime.now(tz=datetime.UTC) - user_data["last_interaction"] < datetime.timedelta(
            seconds=BotConfig.get_config("COOLDOWN_STANDARD")
        )

    async def update_cooldown(self, user_id: int) -> None:
        """Update the cooldown timestamp for the user."""
        user_data = self.cooldowns[user_id]
        user_data["count"] += 1
        if user_data["count"] >= BotConfig.get_config("COOLDOWN_RATE"):
            user_data["last_interaction"] = datetime.datetime.now(tz=datetime.UTC)

    async def reset_cooldown_if_needed(self, user_data: dict) -> None:
        """Reset cooldown count if the cooldown period has passed."""
        if datetime.datetime.now(tz=datetime.UTC) - user_data["last_interaction"] >= datetime.timedelta(
            seconds=BotConfig.get_config("COOLDOWN_STANDARD")
        ):
            user_data["count"] = 0

    # Listeners ----------------------------------------------------------------

    @commands.Cog.listener("on_message")
    async def listen_to_messages(self, message: disnake.Message) -> None:
        """Listen for prompts for markov generation.

        Parameters
        ----------
        message : str
            The message to process for mentions.

        """
        if (
            message.content.startswith("?")  # messages need to start with ?
            and len(message.content.split()) == 1  # message needs to be a single word
            and message.content.count("?") == 1  # ignore things like ?? or ???
            and len(message.content) != 1  # ignore single ?
        ):
            self.messages.append(message)
            if await self.is_on_cooldown(message.author.id) and message.author.id not in BotConfig.get_config(
                "NO_COOLDOWN_USERS"
            ):
                return
            await self.update_cooldown(message.author.id)
            self.messages.append(
                *await self.send_markov_response(message, message.content.split()[0][1:], dont_tag_user=True)
            )
            return

    @commands.Cog.listener("on_message")
    async def add_message_to_markov_training_sample(self, message: disnake.Message) -> None:
        """Record messages for the Markov chain to learn.

        Parameters
        ----------
        message: disnake.Message
            The message to record.

        """
        if not BotConfig.get_config("ENABLE_MARKOV_TRAINING"):
            return
        if message.author.bot:
            return
        self.markov_training_sample[message.id] = message.clean_content

    @commands.Cog.listener("on_raw_message_delete")
    async def remove_message_from_markov_training_sample(self, payload: disnake.RawMessageDeleteEvent) -> None:
        """Remove a deleted message from the Markov training sentences.

        Parameters
        ----------
        payload: disnake.RawMessageDeleteEvent
            The payload containing the message.

        """
        if not BotConfig.get_config("ENABLE_MARKOV_TRAINING"):
            return

        message = payload.cached_message

        # if the message isn't cached, for some reason, we can fetch the channel
        # and the message from the channel
        if message is None:
            channel = await self.BotConfig.fetch_channel(int(payload.channel_id))
            try:
                message = await channel.fetch_message(int(payload.message_id))
            except disnake.NotFound:
                Markov.logger.exception("Unable to fetch message %d", payload.message_id)
                return

        self.markov_training_sample.pop(message.id, None)

    # Slash commands -----------------------------------------------------------

    @slash_command_with_cooldown(
        name="remove_markov_messages",
        description="Remove all of the bot's messages since the last restart.",
        dm_permission=False,
    )
    async def remove_messages(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Clean up the bot responses.

        This will delete all of the bot's responses in the chat, since its
        last restart.

        Parameters
        ----------
        inter : disnake.ApplicationCommandInteraction
            The interation object representing the user's command interaction.

        """
        if len(self.messages) == 0:
            await inter.response.send_message("There is nothing to remove.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        for i in range(0, len(self.messages), 100):
            messages_to_delete = list(self.messages[i : i + 100])
            await inter.channel.delete_messages(messages_to_delete)
        self.messages.clear()
        await inter.delete_original_message()
        await inter.channel.send(
            f"{inter.user.display_name} made me delete my messages :frowning2:",
        )

    @slash_command_with_cooldown(
        name="update_markov_chain",
        description="force update the markov chain for /sentence",
        dm_permission=False,
    )
    async def update_markov_chain(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Update the Markov chain model.

        If there is no inter, e.g. not called from a command, then this function
        behaves a bit differently -- mostly that it does not respond to any
        interactions.

        The markov chain is updated at the end. The chain is updated by
        combining a newly generated chain with the current chain.

        Parameters
        ----------
        inter: disnake.ApplicationCommandInteraction
            The interaction to possibly remove the cooldown from.

        """
        if not BotConfig.get_config("ENABLE_MARKOV_TRAINING"):
            await inter.response.send_message("Updating the Markov Chain has been disabled.")
        else:
            await inter.response.defer(ephemeral=True)

        await update_markov_chain_for_model(
            inter,
            markov.MARKOV_MODEL,
            list(self.markov_training_sample.values()),
            BotConfig.get_config("CURRENT_MARKOV_CHAIN"),
        )
        self.markov_training_sample.clear()

        await inter.edit_original_message("Markov chain has been updated.")

    @tasks.loop(hours=6)
    async def markov_chain_update_loop(self) -> None:
        """Get the bot to update the chain every 6 hours."""
        if not BotConfig.get_config("ENABLE_MARKOV_TRAINING"):
            return
        await update_markov_chain_for_model(
            None,
            markov.MARKOV_MODEL,
            list(self.markov_training_sample.values()),
            BotConfig.get_config("CURRENT_MARKOV_CHAIN"),
        )
        self.markov_training_sample.clear()


def setup(bot: commands.InteractionBot) -> None:
    """Set up the entry function for load_extensions().

    Parameters
    ----------
    bot : commands.InteractionBot
        The bot to pass to the cog.

    """
    bot.add_cog(Markov(bot))
