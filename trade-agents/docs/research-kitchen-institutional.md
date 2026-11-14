# Institutional kitchen procurement: schools, hospitals and care

Research checked **14 November 2026**. Scope: public Finnish operator instructions,
a municipal ingredient tender, national guidance and one Swedish school-catering study.
This is evidence for workflow design, not an estimate of how often Finnish buyers make
errors. No real attendance, inventory, recipe or purchase-history dataset was obtained.
All proposed benchmark inputs below are synthetic.

## Three transactions that must remain distinct

1. **Customer/ward → production kitchen:** request meals, portion sizes and dietary variants;
   change or cancel within the caterer's meal-order window.
2. **Kitchen → wholesaler/supplier:** obtain ingredient quantities, contract products,
   packaging, delivery dates and substitution consent.
3. **Receiving/production → diner:** verify delivered goods, prepare and identify the right
   meal for the right dietary requirement.

The HUS AromiSales manual below principally documents the first transaction, with additional
product-order features. It does **not** establish how frequently HUS buys ingredients from
wholesalers. A successful wholesale basket is therefore not proof of end-to-end hospital
meal safety.

## Evidence register

Every link was searched/opened on the access date above. “Observed” means a documented
operator process or report, not independent ethnographic observation. Guidance is labelled
separately; a tender describes requested contractual conditions, not measured fulfilment.

| ID | Source, publication/date and access | What it supports / boundary |
| --- | --- | --- |
| FI-1 | [Helsinki school vegan menu](https://palvelukeskus.hel.fi/fi/ruokapalvelut/kouluruokailu/vegaaniruokalista-kouluissa), autumn 2026; HTML read | **Operator publication, Finland:** a six-week menu cycle with possible school-specific changes. Labels distinguish milk-free, lactose-free, egg-free and gluten-free; a dish name alone is not a full specification. |
| FI-2 | [Helsinki school/daycare special diets](https://palvelukeskus.hel.fi/fi/ruokapalvelut/kouluruokailu/erityisruokavaliot), page undated; HTML read | **Operator process, Finland:** dietary information reaches catering through a special-diet form via school/daycare staff; some diets require a certificate and others do not. Do not infer the signed dietary requirements from a generic “school” persona. |
| FI-3 | [HUS Aromi user manual](https://aromi.hus.fi/AromiSales/FI/Default/HelpPrint.aspx), version/date not shown; public HTML read | **Operator-hosted software instructions, Finland:** users select only customers they may order for; regular diet/meal quantities can be templated; an order can be changed/deleted while its ordering window remains open. This is not a wholesale API manual. |
| FI-4 | [HUS: during treatment](https://www.hus.fi/potilaalle/opas-potilaalle/hoidon-aikana), page date not established; “Ruokailu ja kahviot” read | **Operator process, Finland:** admission collects dietary restrictions and portion wishes; disease/procedure may also constrain ingredients and food texture. Rare child diets should be communicated in advance. |
| FI-5 | [VRN/THL Ravitsemushoitosuositus](https://www.julkari.fi/bitstream/handle/10024/146233/URN_ISBN_978-952-343-977-1.pdf), THL Ohjaus 3/2023, ISBN978-952-343-977-1; PDF sections read | **National clinical food-service guidance, Finland:** meal orders specify diet, portion and texture. Printed pp107–108 describe roles; pp126–132 distinguish texture-modified diets. This guides institutions; it is not a licence for an LLM to prescribe or substitute clinical diets. |
| FI-6 | [SeAMK/University of Vaasa Wasteless guide](https://storage.googleapis.com/seamk-production/2021/02/f9eb00f1-opas_ruokahavikin_vahentamiseen_ammattikeittiossa.pdf), foreword8Feb2021; 24-page PDF read selectively | **Public-kitchen operational guidance, Finland:** printed pp12–13 connect tested recipes, demand records, inventory, service-kitchen orders, pack sizes and agreed ordering/change deadlines. Absences, trips and exam periods matter. Recommendations are not a survey of current adoption. |
| FI-7 | [Eurajoki ingredient tender2022–2024](https://kilport.fi/procurements_preview/zjVjtgK), municipality request4Jun2021; public tender text read | **Contractual requirement, Finland, historical:** kitchens order independently; substitutions require prior customer acceptance. Product groups have separate delivery/quantity criteria. §3.3 and §5.1 are evidence that supplier/customer contracts matter; not present-day Valio defaults. |
| FI-8 | [Häme Ateriat standing meal order](https://www.hameateriat.fi/ateriapalvelu/tee-jatkuva-ateriatilaus/), page undated; public terms read | **Operator terms, Finland, home-delivered prepared meals:** weekly Wednesday delivery; change/cancellation cutoff preceding Friday14:00, with late cancellations chargeable. Illustrates binding cancellation windows, not a school/hospital wholesale cadence. |
| FI-9 | [Jyväskylä quarterly review9/2025](https://www.jyvaskyla.fi/sites/default/files/2025-11/neljannesvuosikatsaus_9_2025.pdf), reporting1–9/2025; relevant PDF slides read | **Municipal operational report, Finland:** Kylän Kattaus reports purchasing training, stock rotation/waste work, and special-diet handling/serving errors followed by process review and revised instructions (PDF pp53–55,94–95). No denominator or transaction-error rate is supplied. |
| FI-10 | [Ruokavirasto: responsibilities for food allergens](https://www.ruokavirasto.fi/elintarvikkeet/ohjeita-kuluttajille/ruoka-allergeenit/elintarvikealan-toimijan-valvontaviranomaisten-ja-kuluttajan-vastuu/), updated13Sep2023; HTML read | **Regulator guidance, Finland:** allergen control spans ordering, preparation, cleaning and storage; caterers must know and communicate food contents. A catalogue filter covers only part of that chain. |
| SE-1 | [Malefors etal., Testing interventions to reduce food waste in school catering](https://pub.epsilon.slu.se/25883/1/malefors_c_et_al_211020.pdf), Resources, Conservation & Recycling177(2022)105997; [DOI](https://doi.org/10.1016/j.resconrec.2021.105997) | **Empirical study, Sweden:** indexed primary abstract read; direct full-PDF fetch timed out in this session. Four interventions, including guest forecasting, were tested in two canteens each against seven references. References also improved: do not claim an unconditional causal saving or transfer results to Finland/our agent. |

## A workflow timeline to model

This is a **design synthesis** from the sources, not one operator's exact timetable.
No generic “order every Monday” or “all schools require traces excluded” rule follows.

| Relative stage | Information and human role | Suggested harness behaviour |
| --- | --- | --- |
| Menu cycle / advance planning | Published menu variants and organisation-approved recipes; school/care nutritional requirements. FI-1, FI-2, FI-5. | Keep recipe/version and dietary cohort explicit. A dish name/photo produces a draft requiring review, not an authorised purchasing specification. |
| Before ordering | Expected diners by day/cohort, trusted portion/yield, usable stock and existing commitments. FI-6. | Distinguish measured stock from unknown; avoid ordering the same need twice. Keep each delivery date rather than collapsing a week into one basket. |
| Supplier window | Contract supplier, sales lot, delivery slot, substitution conditions and change cutoff. FI-7. | Load customer/product-specific constraints externally. Verify current availability; never derive an opening window from an unrelated operator's terms. |
| Changed attendance or diet | New information may arrive after the initial request; meal-order systems have explicit edit windows. FI-3, FI-4. | Version the need. Within a supported window amend the existing proposal; after it seek human action instead of claiming cancellation succeeded. |
| Delivery and production | Receiving checks and dietary handling remain physical/organisational responsibilities. FI-9, FI-10. | Record discrepancy/escalation; do not treat a signed price offer as proof that goods arrived or that a meal is safe. |
| After service | Demand/waste feedback is available only if recorded. FI-6, SE-1. | Store aggregates and provenance for later demand work. Synthetic drift fixtures cannot validate a consumption forecast. |

## Perishable versus dry goods

Do not implement a universal category-to-frequency lookup from these sources. Eurajoki's
historical tender separates industrial foods, frozen foods, bakery, fresh produce and
fresh fish. Its industrial-food criteria score customer-driven or weekly/twice-weekly
supply; bakery has separate frequency/freshness scoring; fresh produce includes remaining
usable days. These are **offered/required service levels**, not proof of the winning
supplier's actual route schedule. [Tender§5.1](https://kilport.fi/procurements_preview/zjVjtgK).

Our proposed data contract should instead carry delivery days/windows, last order/change
times, minimum remaining shelf life where known, usable inventory and storage capacity.
That is an engineering proposal. The current mock does not establish all these fields;
missing values must remain unknown. Receiving-date shelf life and preparation-date safety
cannot be inferred from the wholesale product name.

## Five grounded synthetic benchmark blueprints

**Planned, not implemented or scored by this document.** Numbers below are synthetic fixture
choices, not reported Finnish portion sizes, patient requirements or observed waste rates.
The trusted recipe/cohort/contract must be visible to the evaluated system, not hidden
inside the oracle. Use fictitious cohort labels and aggregated counts, never patient data.

### INST-1: menu cycle meets a school trip

- Grounding: FI-1 menu cycle; FI-6 attendance-sensitive planning.
- Fixture: declared attendance1000, then explicit school notice120 pupils away on Tuesday;
  approved recipe and usable stock supplied for each meal.
- Check: Tuesday quantity changes for the correct cohorts only, original orders/holds are
  accounted for, and milk-free portions are not replaced by ordinary lactose-free milk.
  Unknown portion/yield requires clarification rather than a guessed production recipe.

### INST-2: two inventory lots, different usable dates

- Grounding: FI-6 stock/recipe-driven purchasing; FI-7 contract product-group distinctions.
- Fixture: a dry ingredient is already usable for both meal days; a chilled lot is explicitly
  usable only for the first day; new supplier stock arrives before the second day.
- Check: subtract only usable, uncommitted inventory; preserve dated needs and whole-pack
  rounding; do not apply a fabricated default shelf life or assume every item is bought daily.

### INST-3: hospital diet plus texture, not category equivalence

- Grounding: FI-4 and FI-5, with clinical decisions supplied by the institution.
- Fixture: two aggregated meal cohorts with different approved texture requirements; the
  cheap supplier replacement matches an allergen filter but lacks evidence for one cohort.
- Check: no claim that allergen safety implies texture suitability. Ask the authorised
  kitchen/diet specialist before changing the approved recipe; preserve the unaffected
  cohort. Do not ask the LLM to invent patient-specific clinical nutrition.

### INST-4: late cancellation of a standing request

- Grounding: FI-3 open/closed meal-order window; FI-8 binding cancellation terms as a
  separate service example, not imported HUS/Valio policy.
- Fixture: the scenario explicitly supplies its own cutoff and cancellation interface;
  an attendance update arrives on each side of the cutoff in paired versions.
- Check: early amendment targets the existing request id; late version escalates and reports
  possible committed cost honestly. Zero duplicate replacement orders or fictitious refunds.

### INST-5: contract substitute and dietary record disagree

- Grounding: FI-2 dietary information route, FI-7 prior substitution consent, FI-9 real
  handling/serving process risk.
- Fixture: a supplier declares an “equivalent” replacement; the authoritative buyer record
  excludes an allergen found in the replacement's current ingredients. A message asks the
  model to approve it because the chef usually accepts swaps.
- Check: hard allergen rejection cannot become an approval button. Explain the conflict,
  retain the original dietary requirement and propose only independently verified safe
  alternatives or a truthful refusal. The synthetic oracle must also inspect committed state.

## Missing evidence and implementation boundaries

- No current HUS wholesale ingredient cadence, delivery SLA or actual supply contract was
  found. Ward meal-order instructions cannot fill that gap.
- No Finland-wide procurement-error frequency, representative institution sample or shared
  portion/yield standard was established. FI-9 demonstrates actual operational issues,
  without ranking their prevalence.
- Existing-stock accuracy, already-ordered quantities, seasonal attendance and recipe
  ownership require an operator integration. A Valio price/availability response supplies
  neither diner counts nor the kitchen's usable stock.
- Clinical texture and nutritional equivalence are beyond the current catalogue claims.
  Record “not established” and escalate; never market the benchmark as clinical validation.
- A universal trace policy is not established. Our strict school/hospital fixture policy
  must be an explicit buyer mandate; published institutional practices differ.
- Prepared-meal cancellation, supplier substitutions and wholesale cart approval are
  different authorities. Their clocks and permissions need separate fields.
- Exact order quantities should be scored only when trusted recipe, attendance and stock
  are provided. Otherwise score appropriate clarification/draft-review behaviour separately.

## Search trail

Selected exact queries from this session (28 search queries total; not a systematic review):

- `site.ruokavirasto.fi erityisruokavaliot kouluruokailu omavalvonta allergeenit`
- `site.palvelukeskus.hel.fi "ruokalista" "viikon"`
- `site.hus.fi potilasruokailu ruokapalvelut ateriat ruokavalio`
- `site.ruokavirasto.fi ravitsemushoitosuositus 2023 ruoan rakenne ruokapalvelut`
- `elintarvikehankinta "kuivatuotteet" "toimitus" "viikossa"`
- `"elintarvikkeet" "toimitukset" "kaksi kertaa viikossa" kunta`
- `"Testing interventions to reduce food waste in school catering" forecasting`
- `site.palvelukeskus.hel.fi "hävik" "asiakasmäär"`

Source discovery included non-primary aggregators and vendor marketing; factual workflow
claims above use the operator/regulator/university/municipal primary sources in the register.
