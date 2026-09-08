import { verifyKey } from "discord-interactions";

const REPO = "eist-radio/eist-tools";
const GH_API = `https://api.github.com/repos/${REPO}/actions/workflows`;

const OOPS = "oop that didn't work ¯\\_(ツ)_/¯";

// Discord interaction types
const PING = 1;
const APPLICATION_COMMAND = 2;
// Discord response types
const PONG = 1;
const CHANNEL_MESSAGE = 4;

const EPHEMERAL = 64;

// Discord permission bit for ADMINISTRATOR.
const ADMINISTRATOR = 1n << 3n;

// Slash commands, each mapped to the archive.yml inputs it dispatches.
// Register them with `npm run register` after adding one here.
const COMMANDS = {
  cleanup: {
    inputs: { mode: "full", weeks: "8", dry_run: "false" },
    ack: "Making some space...",
  },
  "delete-archived": {
    // Deleting from Radiocult cannot be undone, so this one is admin-only on
    // top of whatever DISCORD_ALLOWED_ROLE_IDS says.
    inputs: { mode: "delete-archived", dry_run: "false" },
    ack: "Taking out the bins...",
    adminOnly: true,
  },
};

function reply(content, flags = 0) {
  return Response.json({
    type: CHANNEL_MESSAGE,
    data: flags ? { content, flags } : { content },
  });
}

async function dispatchWorkflow(env, workflow, inputs = {}) {
  const resp = await fetch(`${GH_API}/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "eist-tools-worker",
    },
    body: JSON.stringify({ ref: "main", inputs }),
  });
  if (!resp.ok) {
    const body = await resp.text();
    console.error(`Dispatch ${workflow} failed: ${resp.status} ${body}`);
  }
  return resp.ok;
}

// Discord sends the invoking member's computed permissions with every guild
// interaction, so this needs no role IDs and survives a role being renamed or
// recreated. A DM has no member and so never counts as admin.
function isAdmin(interaction) {
  const permissions = interaction.member?.permissions;
  if (!permissions) return false;
  try {
    return (BigInt(permissions) & ADMINISTRATOR) === ADMINISTRATOR;
  } catch {
    return false;
  }
}

// An empty allowlist means anyone in the server can run the command.
function isAllowed(interaction, env) {
  const allowed = (env.DISCORD_ALLOWED_ROLE_IDS || "")
    .split(",")
    .map((r) => r.trim())
    .filter(Boolean);
  if (allowed.length === 0) return true;
  const roles = interaction.member?.roles || [];
  return roles.some((r) => allowed.includes(r));
}

export default {
  // Hourly trigger for the slot check, unchanged.
  async scheduled(event, env, ctx) {
    const ok = await dispatchWorkflow(env, "check-slot.yml");
    console.log(ok ? "Dispatched check-slot workflow" : "check-slot dispatch failed");
  },

  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("eist-tools worker", { status: 200 });
    }

    const signature = request.headers.get("x-signature-ed25519");
    const timestamp = request.headers.get("x-signature-timestamp");
    const body = await request.text();

    if (!signature || !timestamp) {
      return new Response("Missing signature headers", { status: 401 });
    }

    // Discord requires a 401 on a bad signature. It sends a deliberately
    // invalid request when you save the endpoint URL and rejects the endpoint
    // if that request gets anything else back.
    const valid = await verifyKey(body, signature, timestamp, env.DISCORD_PUBLIC_KEY);
    if (!valid) {
      return new Response("Bad request signature", { status: 401 });
    }

    const interaction = JSON.parse(body);

    if (interaction.type === PING) {
      return Response.json({ type: PONG });
    }

    if (interaction.type !== APPLICATION_COMMAND) {
      return Response.json({ type: PONG });
    }

    const command = COMMANDS[interaction.data?.name];
    if (!command) {
      return reply(OOPS, EPHEMERAL);
    }

    if (!isAllowed(interaction, env)) {
      return reply("You don't have permission to run that.", EPHEMERAL);
    }

    if (command.adminOnly && !isAdmin(interaction)) {
      return reply("Only server admins can run that.", EPHEMERAL);
    }

    const user = interaction.member?.user || interaction.user || {};
    const requestedBy = user.username || "someone";

    // Discord closes the interaction after 3 seconds and a live run takes far
    // longer than that, so acknowledge now and let the workflow post the result
    // to the programming channel webhook when it finishes.
    const ok = await dispatchWorkflow(env, "archive.yml", {
      ...command.inputs,
      notify: "true",
      requested_by: requestedBy,
    });

    if (!ok) return reply(OOPS);

    return reply(command.ack);
  },
};
