// One-off: registers the /cleanup slash command with Discord.
//   DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=... node register-commands.js
// Guild-scoped registration appears instantly; global takes up to an hour.

const { DISCORD_APP_ID, DISCORD_BOT_TOKEN, DISCORD_GUILD_ID } = process.env;

if (!DISCORD_APP_ID || !DISCORD_BOT_TOKEN) {
  console.error("Set DISCORD_APP_ID and DISCORD_BOT_TOKEN.");
  process.exit(1);
}

const url = DISCORD_GUILD_ID
  ? `https://discord.com/api/v10/applications/${DISCORD_APP_ID}/guilds/${DISCORD_GUILD_ID}/commands`
  : `https://discord.com/api/v10/applications/${DISCORD_APP_ID}/commands`;

const commands = [
  {
    name: "cleanup",
    description: "Archive old Radiocult media to Drive and free up storage",
    type: 1,
  },
];

const resp = await fetch(url, {
  method: "PUT",
  headers: {
    Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify(commands),
});

console.log(resp.status, await resp.text());
