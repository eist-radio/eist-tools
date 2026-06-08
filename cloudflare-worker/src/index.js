export default {
  async scheduled(event, env, ctx) {
    const resp = await fetch(
      "https://api.github.com/repos/eist-radio/eist-tools/actions/workflows/check-slot.yml/dispatches",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "eist-check-slot-worker",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );

    if (resp.ok) {
      console.log(`Dispatched check-slot workflow (${resp.status})`);
    } else {
      const body = await resp.text();
      console.error(`Failed to dispatch: ${resp.status} ${body}`);
    }
  },
};
