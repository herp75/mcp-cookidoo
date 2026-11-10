# Device modes: analysis (Phase 4)

Comparison of this server's guided-cooking implementation (`schemas.py`, `guided.py`) with
[tobilektri/mcp-cookidoo](https://github.com/tobilektri/mcp-cookidoo) (MIT; `modes.py`,
`thermomix.py`, `recipes.py`, fixtures), to decide what to port for TM6/TM7 device awareness.
Written before any Phase 4 code, as the plan requires.

**Sources and their weight.**
- *This server*: limits documented as "those the Cookidoo API accepts" (upstream v1.1.0), i.e.
  what the API did not reject.
- *tobilektri*: limits taken from the **Cookidoo recipe editor** (its JavaScript,
  pl-customer-recipes 1.174.12, and two saved editor pages `editor_tm6.html` /
  `editor_tm7.html`), plus 10 steps built in the editor dialog and saved with PATCH on
  2026-10-20 (`fixtures/editor_modes.json`). That is what Cookidoo's own UI lets a user create.

Neither was verified live by me. Where they disagree, the editor is the stronger evidence of
what the Thermomix and the Cookidoo app handle; the API accepting a value only shows it is
stored.

## 1. Input format: different by design

| | This server | tobilektri |
|---|---|---|
| Step input | Structured JSON: `{"text": "... {settings} ...", "settings": [{"mode": "dough", "time": 120}], "ingredients": [{"text": ..., "ingredient": ...}]}` | German free text parsed with regular expressions: `"Teig kneten/2 Min."`, `"3 Min./120°C/Linkslauf/Stufe 1"` |
| Languages | fr / en / de rendering, any account language | German only (parser and labels) |
| Ingredient links | Explicit: words of the text → ingredient line | Implicit: verbatim ingredient lines found in the text |
| Unrecognised settings | Validation error | Left as plain text, with a warning |
| Read back (`get_custom_recipe`) | Same JSON format (`from_raw`) | Text with written settings |

**Decision (per plan): this server's JSON format stays canonical.** Nothing of tobilektri's
text parser is ported; only its knowledge: limits per mode and device, the live mode catalog,
and the wire details below.

## 2. Mode and value limits

| Setting | This server today | Cookidoo editor (tobilektri) | Difference |
|---|---|---|---|
| **manual** time | 1 s – 99 h 59 min | 1 s – **99 min** (5940 s) | editor much stricter |
| manual time grid | any second | ≤ 5 min: 10 s steps; ≤ 20 min: 30 s; then whole minutes | tobilektri only **warns** |
| manual temperature | 37–120 °C, `varoma` | 37–120 °C; **`varoma` only on TM6** | TM7 has no Varoma temperature (it has the steaming mode) |
| manual speed with heat | any | **max speed 6** when a temperature is set | new rule |
| **dough** time | 1 s – 99 h 59 | 1 s – **20 min** | stricter |
| **browning** | TM6/TM7 (prompt says so), time 1 s – 30 min required, 140–160 °C, gentle/intense | **TM7 only**; time optional (editor default 10 min); same temperatures/powers | device; time default |
| **steaming** | any device, time 1 s – 99 h 59, speed soft–5, reverse, accessory | **TM7 only**; time 1 s – 99 min; same speeds/accessories | device; time |
| **turbo** time | 0.5 / 1 / 2 s | 0.5 / 1 / 2 s | same |
| turbo pulses | 1–10, default 1, **always sent** | **TM7 only**, 1–9, sent only when given | device; max; wire (§3) |
| **rice_cooker** | no settings | no settings | same |
| **warm_up** | 37–120 °C, any speed | **37–90 °C**, speed **soft / 1 / 2** (default soft) | stricter |
| **blend** | time 1 s – 99 h 59, any speed | time **10 s – 5 min**, speed **6 – 8** | stricter |
| Devices | `COOKIDOO_DEVICE` = TM5 / TM6 / TM7, set as the recipe's tool on create | TM6 / TM7; device of a recipe = TM7 if TM7 is among its tools | TM5 not covered by tobilektri |

The editor pages for TM6 and TM7 both list all seven modes, with browning and steaming marked
`tm7-mode`; the editor shows those two only in TM7 recipes.

## 3. What is sent to Cookidoo (wire format)

| Case | This server sends | Editor saved | Difference |
|---|---|---|---|
| Varoma temperature | `"temperature": {"value": "varoma"}` | `{"value": "varoma", "unit": "C"}` | **unit missing** |
| Turbo on TM6 | `"data": {"time": 1, "pulseCount": 1}` | `{"time": 1}` | `pulseCount` should not be sent on TM6 |
| Steaming, no reverse | `"direction": "CW"` | not in the fixtures (only a reverse case) | unknown, leave as is |
| Mode/position, TTS fields, accessory values (`Varoma`, `SimmeringBasket`, `VaromaAndSimmeringBasket`), powers (`Gentle`/`Intense`), UTF-16 positions | same | same | none |

## 4. Step text

| | This server | Editor |
|---|---|---|
| Manual | `3 Min./120°C/Linkslauf/Stufe 1` (words) | `3 Min./120°C//Stufe 1` (icon-font glyphs for reverse and soft speed) |
| Modes | `Pürieren/1 Min./Stufe 8`, `Anbraten/10 Min./160°C/Leicht` (all values) | `Pürieren /1 Min.`, `Anbraten ` (title + glyph + one value) |
| `;` | kept | replaced by full-width `；` |

The text is what the user reads; the machine is preset from the annotation. Our words are
readable in every client and in every language, the glyphs only render in Cookidoo's icon font.
**Proposal: keep our text** (no glyphs), and only drop `1x` for a single turbo pulse.

## 5. Live mode catalog

tobilektri reads the editor page `/created-recipes/{locale}/{id}/edit/ingredients-and-preparation-steps`
(HTML, parsed with the stdlib `HTMLParser`): offered modes, which are TM7-only, speeds,
temperatures, turbo times, pulse counts, accessories, browning powers, and localised labels.
Cached 1 h, built-in fallback (snapshot of 2026-10-20).

- It needs the ID of **an existing custom recipe** of the account (the newest is used); with
  none, only the fallback is available.
- The limits per mode (time ranges, allowed speeds) are **not** in the HTML; they come from the
  editor's JavaScript and stay hard-coded (`MODE_RULES`).
- The HTML gives localised mode titles for the account's language, which could later replace
  hand-written word lists for languages other than fr/en/de. Not proposed for this phase.

**Proposal:** port the parser and fallback as planned, hardened: accept only known mode names
and in-range values, ignore everything else, never fail an upload because of it, and say in
the tool output when the built-in list was used. The catalog only *restricts* (a mode Cookidoo
no longer offers is refused); it never widens the hard-coded limits.

## 6. Does the step JSON format change?

**Field names and structure: no.** `Step`, `settings`, `mode`, all fields stay as they are, and
existing JSON keeps the same meaning. What changes is **which values are accepted**, and that
depends on the device:

- valid today, refused for any device under editor limits: manual > 99 min, dough > 20 min,
  blend outside 10 s – 5 min or speed ≠ 6–8 (the README's own example uses `"speed": "10"`),
  warm_up above 90 °C or speed > 2, turbo with 10 pulses, heating above speed 6;
- valid today, refused **for TM6** only: browning, steaming, turbo pulses > 1;
- refused **for TM7** only: Varoma as a manual temperature.

So some recipe JSON accepted by v1.1.0 would be rejected. This is the case the plan says to stop
for. `test_guided.py` currently asserts some of those values (blend speed 10, Varoma without
unit, turbo pulses without a device), so "all existing tests pass unchanged" and "adopt the
editor limits" cannot both hold.

## 7. Decisions needed

1. **Strictness.** (a) Enforce the editor limits as validation errors, per device
   *(recommended: a recipe the editor cannot produce may not run correctly on the machine)*;
   (b) accept today's wider values but return warnings; (c) enforce only the device rules
   (TM7-only modes, Varoma on TM6, pulses on TM7) and keep the other limits as they are.
2. **Reading existing recipes.** `get_custom_recipe` stays lenient in any case: recipes already
   on Cookidoo with out-of-range values are returned as they are, and only re-validated when
   saved again. *(Recommended, no alternative proposed.)*
3. **Wire fixes**: send `"unit": "C"` with Varoma and omit `pulseCount` unless pulses > 1
   (TM7). *(Recommended; changes two expectations in `test_guided.py`.)*
4. **Device when not given.** Proposed order: `device` argument → for `update_custom_recipe`,
   the recipe's own tools (TM7 if listed) → `COOKIDOO_DEVICE` if set → linked devices from
   `list_devices` (TM7 if only a TM7 is linked) → TM6. With both a TM6 and a TM7 linked:
   **TM6 rules** (recommended, works on both) or ask the model to pass `device`.
5. **TM5.** `COOKIDOO_DEVICE` accepts TM5 today, but neither implementation knows its limits
   (no modes in the editor for it). Proposal: TM5 recipes allow manual settings only, with the
   TM6 limits, and no modes. Alternative: drop TM5.
6. **Step text**: keep words (§4), no icon glyphs. *(Recommended.)*

## 7a. Decisions (2026-11-10, repository owner)

1. **Enforce the editor limits per device** (option a).
2. Reading stays lenient; recipes are only checked when saved again.
3. `pulseCount` only for more than one pulse. **Varoma `unit`:** not done. cookidoo-api's
   serializer always drops the unit for Varoma (`Cookidoo._temperature_to_json`); upstream
   v1.1.0 uploaded Varoma steps without it, so Cookidoo accepts both. A test documents the
   library behaviour; a change belongs in cookidoo-api.
4. Default device as proposed; with TM6 and TM7 linked: **TM6 rules**.
5. **TM5: manual settings only**, with the TM6 limits.
6. Step text stays in words.

Keeping the per-mode limits current: the live catalog covers modes, TM7-only marks and value
lists at runtime. The per-mode ranges come from the editor's JavaScript and stay a snapshot;
the Phase 5 scheduled live check should compare the editor page with the snapshot and record
the editor's script version, and open an issue when either changes.

## 8. Plan once decided

- `devices.py`: `DEVICES`, a `ModeRule` table (from tobilektri, MIT, credited), allowed values
  per device; a `validate(settings, device)` used by `generate_recipe_structure`,
  `upload_custom_recipe`, `update_custom_recipe` (new optional `device`), with errors that name
  the allowed values.
- `get_thermomix_modes(device)` (READ_ONLY): modes, limits, examples, and whether the list came
  from Cookidoo or the built-in snapshot.
- Live catalog parser + 1 h cache + fallback, as in §5; editor HTML fixtures ported from
  tobilektri.
- Prompts (en/fr/de) ask for the target device; README mode table gets a device column.
- Tests: tobilektri's `test_modes.py` / `test_thermomix.py` cases adapted to the JSON format;
  every mode × device accepted/refused; parser robustness (malformed HTML, missing/extra modes,
  injected scripts); round trip `generate → get_custom_recipe`.
