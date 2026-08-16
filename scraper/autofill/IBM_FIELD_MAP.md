# IBM (Avature) application form — measured field map

Everything here was read off the live DOM in a logged-in IBM candidate session,
not inferred. Measured 2026-08-16 against `jobId` **127789** and **129258**, walked
through to the final review page.

**No personal data belongs in this file.** It records form *structure* only.
Answers live in `application_profile.yaml`, which sits outside the repo and is
never committed (see `scraper/config.py`). This repo has already had to redact a
third party's details once — don't reintroduce the problem here.

---

## 1. Flow

```
careers.ibm.com/en_US/careers/JobDetail?jobId=N
  -> "Apply now"
careers.ibm.com/en_US/careers/ApplicationMethods?jobId=N   # Without Resume / Copy & Paste / From Device
  -> 
careers.ibm.com/en_US/careers/JobApplication?jobId=N       # the wizard; direct navigation works, starts at step 1
  -> ... Continue x N ...
careers.ibm.com/en_US/careers/JobApplicationSummary?jobId=N   # TERMINAL — review + Submit
```

### Detect the end by URL, not by button text

The terminal page is `JobApplicationSummary`. Its buttons are
`Back` / `Submit Application` / `Cancel Application`.

Note that **`Cancel Application` also contains the word "Application"** — text
matching on the final step is exactly the kind of fragility that produced the
`get_by_role(name=...)` bug. Use the URL.

### The progress bar is not a step counter

Observed sequence on jobId 129258: **20% -> 40% -> 60% -> 100%**. There is no 80%
step. Step count varies by requisition; never index steps off the percentage.

---

## 2. Two kinds of dropdown — the single most important finding

Getting this wrong fails **silently**: the field looks touched and stays empty.
Same failure class as the react-select bug documented in `platforms/greenhouse.py`.

### 2a. Plain `<select>` — options present in the DOM at load

Setting `.value` and dispatching `input` + `change` **works**.

Confirmed: `32764` (resident of China/S.Korea), `37299-7-N` (Completed?),
`35979_month`, `35979_day`, `10542-1` (relocate), `20400` (hybrid).

### 2b. Dynamic dropdown — underlying `<select>` has 0 or 1 options at load

The visible control is a custom widget that populates on interaction.

- Setting `.value` does **not** work.
- Clicking the rendered `[role=option]` does **not** work.
- Arrow-keys + Enter selects the **wrong item** — it picked
  `United States Minor Outlying Islands` instead of `United States`.

The only reliable sequence is **click the combobox -> type the search text ->
press Enter**.

Confirmed: `10478`, `10499`, `10500`, `18199`, `46593`, `12706`, `12710`.

Duplicate option labels are common — `United States` x2, `Georgia State
University` x2, `Computer Science` x2. Enter takes the first match, correct in
all three cases.

**Always verify by exact equality against the widget's rendered label after the
fill.** A substring check would have accepted `United States Minor Outlying
Islands` for `United States`. Option labels are also not always the phrasing you
expect — the race field's option is bare **`Black`**, not
`Black or African American`.

---

## 3. `element.required` is NOT trustworthy

Gender (`12704`) reports `required=false` in the DOM. The wizard refuses to
advance without it:

```
Gender: This field is required
```

Any "is this field required" logic built on the HTML attribute will be wrong.
The `*` in the label text is the real signal.

### Validation errors are machine-readable and name the field

A rejected `Continue` renders:

| selector | content |
|---|---|
| `.alert--error.WizardFieldError` | "There are some errors, please correct them." |
| `.errorMessage.WizardFieldError` | "(!) This field is required" (per field) |
| `.screenReaderVisibility` | **"Gender: This field is required"** — names the field |

The form element also picks up `form--has-errors`.

**Prefer this over predicting required-ness.** Fill -> Continue -> parse errors ->
fix -> retry. This makes it structurally impossible to silently pass a step that
wasn't actually completed.

---

## 4. Id conventions

- **Radio groups** — `name` is a numeric field id, element id is `<name>_<value>`.
  **`37` == Yes, `38` == No**, consistently portal-wide. Assert this against the
  rendered label before clicking: a flip answers "No" to *are you legally
  authorized to work in the US* on a live application.
- **Repeat groups** — `<groupId>-<colId>-<rowIndex>`, plus a hidden
  `<groupId>-<colId>-sample` template row.
  **Never fill any id containing `-sample`.** It corrupts the submission.
- Ids appear **portal-global, not per-requisition** — `8989`/`8991` recurred
  identically across two jobIds. Two-posting sample; confirm on a third.

---

## 5. Field map

### Step 20% — Talent Network (skippable)

| id | field |
|---|---|
| `8989` | radio; `_38` = "No thanks, continue to apply" — **checked by default** |
| `8991` / `13982[]` / `8990` / `8992` | area of interest / skills / experience level / communities |
| `8993` | checkbox, acknowledge |

Leave defaults, click Continue.

### Step 40% — privacy, personal, education, employment

| id | field | notes |
|---|---|---|
| `20527_1043734` | "I agree", privacy notice | radio, required |
| `32764` | resident of China or South Korea | plain select |
| `32766` | IBM-processing consent | radio — **hidden unless `32764` == Yes**; don't block on it |
| `10473` / `9000` / `10474` | legal first / middle / last | prefilled |
| `9002` | has preferred name | radio; `9003`/`9004` = preferred first/last |
| `9012` | phone | prefilled |
| `10898` | source of candidate | 0 options, not required |

Education repeat group **`37299`** — column meanings read directly from `<label for>`:

| col | field |
|---|---|
| `-1` | Degree name |
| `-2` | Type of Degree (plain select) |
| `-3` | University |
| `-5` | **Start Date** (`input[type=month]`) |
| `-4` | **End date** (`input[type=month]`) |
| `-6` | Education ID |
| `-7` | Completed? (plain select, Yes/No) |

Employment repeat group **`9017`**, and `9016` = "Do you have past working experience?":

| col | field |
|---|---|
| `-1` | Company |
| `-4` | Start date (`input[type=date]`) |
| `-3` | Is current position? (plain select) |
| `-5` | End date (`input[type=date]`) |
| `-2` | Position title |

Identity, address, education and employment all arrive **prefilled from the saved
Avature candidate profile**. Only fill what is genuinely empty.

### Step 60% — screening, EEO, resume

| id | field | widget |
|---|---|---|
| `10478` | How did you hear about this opportunity? | dynamic |
| `10480` | legally authorized to work in US | radio |
| `10774` | requires IBM sponsorship | radio — *revealed* |
| `10481` | "I certify" accuracy attestation | checkbox — *revealed* |
| `10498` | attended university | radio |
| `10499` | university country | dynamic — *revealed* |
| `10500` | university | dynamic — *revealed* |
| `18199` | degree obtained / in progress | dynamic — *revealed* |
| `46593` | study / specialization | dynamic — *revealed* |
| `10519` / `10520` / `10521` | location 1st / 2nd / 3rd choice | text |
| `10522` | consider other locations | radio |
| `10523` | residential differs from permanent | radio |
| `10530` | worked at IBM before | radio |
| `35979_month` / `35979_day` | day + month of birth | plain select, **required** |
| `10542-1` | willing to relocate | plain select |
| `10542-2` | top 3 location preferences | textarea |
| `10542-3` | available start date (Month/Year) | text |
| `20400` | can meet hybrid requirement | plain select |
| `21272` | **Resume / CV** | file, required |

`10478` options: Agency, Campus, Events, IBM Careers website, Job Board,
LimeConnect, Other, Recruiter, Referral, Social.

`21272` accepts: `.csv .doc .docx .fotd .otd .pdf .ppt .pptx .rtf .txt .wps`

### Step 60% — EEO block

| id | field | required? |
|---|---|---|
| `12704` | Gender (`_92` / `_93` / `_94`) | **YES — blocks Continue** |
| `12705` | Ethnicity (`_741422` Hispanic / `_741423` Non-Hispanic) | optional |
| `12706` | Race | dynamic; optional |
| `12707` | Are you a veteran? (`_741283` Yes / `_741284` No / `_741285` decline) | optional |
| `12708` | Protected veteran, same value scheme | optional |
| `12709` | Disability (`_126` yes / `_127` no / `_128` decline) | optional |
| `12710` | Veteran status | dynamic; optional |
| `13602_*` | two "I authorize the use of my responses" consents | optional |

**Gender cannot be skipped.** An earlier draft of this spec said never to touch
the EEO block; that is wrong, because the application cannot be submitted without
gender. Correct rule: **gender is routed from a stored profile value like any
other field — routed, never authored.** The rest stay untouched unless the
profile carries a value.

---

## 6. Cascading reveals

Single-pass field iteration is insufficient. Answering a question spawns new
required questions. Observed on step 60%:

```
10480 "authorized to work in US" = Yes
    -> reveals 10774 "requires sponsorship"
    -> reveals 10481 "I certify"

10498 "attended university" = Yes
    -> reveals 10499 country
        -> reveals 10500 university
            -> reveals 18199 degree
                -> reveals 46593 specialization
```

Loop: scan visible + unanswered + non-`sample` -> fill -> rescan -> repeat until a
pass adds nothing. Cap ~6 passes.

---

## 7. Open items

- Ids confirmed on two requisitions only. Check a third, ideally a non-internship.
- Repeat-group column meanings read from labels on one posting — verify they are
  portal config and not per-requisition.
- `ApplicationMethods` resume-choice path was bypassed (navigated straight to
  `JobApplication`); the "From Device" branch is unmapped.
- No IBM source exists in `ats_config.py`. IBM's search at `ibm.com/careers/search`
  is server-rendered HTML with results in the markup, not in `__NEXT_DATA__`; no
  JSON search endpoint found. Separate spike.
