# Shadow — channels (frozen-contract)

- **Outbound: built.** Governed messaging via Apprise (80+ destinations, 22 first-class) — every outbound
  message flows through `messaging/` (governed, optimal-time-aware via the durable scheduler).
- **Inbound: deferred → W7.** Adapters (Telegram · Slack · WhatsApp · email) + webhook/event ingestion land
  in **W7** (reach/modalities). Per-channel `*.yaml` configs are authored there.
