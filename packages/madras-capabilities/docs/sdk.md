# Capability-creation SDK (T5.1, RFC-0002 §3.5/§12.2)

How a third party — or a future you, six months from now — adds a new capability to Madras's
Capability Catalog: the genome that generates `.tamil`'s own grammar extensions (§3.5). This is
today's internal convention, written up as a public-facing SDK doc for the first time. Three
steps, always in this order.

## 1. Write the capability note

One Markdown file, `Framework/Capabilities/<Name>.md`, with typed frontmatter validated against
the schema in `madras_capabilities.model` (H7's typed-schema fix — `extra="forbid"`, real enums,
not a permissive dict):

```yaml
---
type: capability
id: my_capability          # snake_case, matches the catalog id used in NAME positions in .tamil
category: "Security"       # one of the catalog's real categories
kind: functional            # functional | faculty | ...
build_state: built          # built | partial | planned | frontier | not_applicable | deferred
implements: [security]      # the toolset(s) this capability expands into (AgentConfig.toolsets)
rank_required: intern       # the minimum Rank that may invoke this capability
scopes: [code.read]         # the credential/permission scopes this capability needs
evaluates: []               # which eval dimensions this capability's use is scored on
provenance: "engine + OSS (vendor/repo, LICENSE)"
layer: "[[Tools & Capabilities]]"
source_files:
  - "Engineering/src/madras/tools/builtin/my_tool.py"
  - "Engineering/tests/test_tools/test_my_tool.py"
source_doc: "Framework/Tools & Capabilities.md"
---

# My Capability

One or two sentences: what it does, what gap it fills.
```

**`build_state: built`** is a real gate, not a label: `resolve_toolsets()` (step 3 below) refuses
to resolve any capability whose `build_state` isn't exactly `"built"` — an unbuilt or `frontier`
capability cannot be composed into a live agent, by construction.

## 2. Register it in the generator

Add one tuple to `CAPS` in `scripts/gen_capability_catalog.py` — name, category, kind, build_state,
implements, rank_required, scopes, evaluates, provenance, source_files, source_doc, and a one-line
summary. This is what keeps the note and the generator's own registry from drifting silently
(the conformance suite, `tests/test_lighthouse/test_capability_catalog.py`, checks both agree).

## 3. It's now callable from `.tamil` — with zero grammar changes

This is the whole point of the genome-generates-grammar design (§3.5, proven by T7's
composability test): a new capability needs **no new Kural grammar rule**. It flows through the
existing `capability-call` kernel node exactly like every other capability:

```
goal "use my new capability" {
    govern rank >= 1
    call my_capability()
}
```

`resolve_toolsets(["my_capability"], catalog)` (`madras_capabilities.resolve`) validates the id is
real and built, and expands it into the toolset(s) named in `implements`; `madras.dsl.interpreter`
wires this into a live, governed `AgentConfig` via `spawn_agent_preview()` — no bypass, the same
path a hand-authored agent uses (T5).

## Worked example — `ffi_bridge` (T3.1)

`ffi_bridge` is not a capability-catalog entry itself — it's a **capability kind**, one layer
below what this doc otherwise describes: a way to declare a capability-call that routes through
to a foreign-language function (Python, today) rather than a Madras-native tool. It's the worked
example for *why* the kernel stays small (D10/D59): rather than invent a new kernel primitive
for "call out to Python," `ffi_bridge` is just `capability_kind="ffi_bridge"` on the same `Call`
node every other capability uses:

```
goal "process a CSV upload" {
    govern rank >= 2
    bind rows = ffi python parse_csv(path)
    call notify("done")
}
```

Grammar: `packages/tamil-lang/src/tamil_lang/kural.lark`'s `ffi_call` rule. AST:
`tamil_lang.ast.Call` with `capability_kind="ffi_bridge"` + `lang="python"`. Declaring/parsing an
FFI call is open (this package, Apache-2.0); the governed *execution* of that call — the actual
Python function, its rank/scope check, its sandboxing — is a Compiler/Agent-OS concern in the
closed tree, per the D9/D58 open/closed split.

---
*T5.1, `tamil-and-backend-spatial.md`. → [[Agent OS]] · [[RFC-0002]] · [[Capability Catalog]].*
