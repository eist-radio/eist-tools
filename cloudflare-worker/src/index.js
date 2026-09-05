import { verifyKey } from "discord-interactions";

const REPO = "eist-radio/eist-tools";
const GH_API = `https://api.github.com/repos/${REPO}/actions/workflows`;

const OOPS = "oops that didn't work ¯\\_(ツ)_/¯";

// Discord interaction types
const PING = 1;
const APPLICATION_COMMAND = 2;
// Discord response types
const PONG = 1;
const CHANNEL_MESSAGE = 4;

const EPHEMERAL = 64;

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

    if (interaction.data?.name !== "cleanup") {
      return reply(OOPS, EPHEMERAL);
    }

    if (!isAllowed(interaction, env)) {
      return reply("You don't have permission to run that.", EPHEMERAL);
    }

    const user = interaction.member?.user || interaction.user || {};
    const requestedBy = user.username || "someone";

    // Discord closes the interaction after 3 seconds and a live cleanup runs
    // far longer than that, so acknowledge now and let the workflow post the
    // result to the programming channel webhook when it finishes.
    const ok = await dispatchWorkflow(env, "archive.yml", {
      mode: "full",
      weeks: "8",
      dry_run: "false",
      notify: "true",
      requested_by: requestedBy,
    });

    if (!ok) return reply(OOPS);

    return reply(`Cleanup started by ${requestedBy}. I'll report back here when it's done.`);
  },
};
