---
spec_version: "1"
task_id: "kitchen-workflows"
research_stage: "internet"
searches_conducted: 8
sources_cited: 14
papers_discovered: 0
date_completed: "2026-11-14"
status: "partial"
---
# Professional kitchen workflows: from menu to purchasing and receiving

## Task Objective

Consolidate primary evidence for professional-kitchen purchasing and use it to define
credible agent workflows and future benchmark cases. Distinguish menu cycles, meal-order
windows, wholesaler lead times, customer delivery calendars and actual kitchen ordering
frequency. The work extends the earlier procurement research with operational examples,
not new claims about model performance. Status remains partial: no representative
Finnish transaction-error denominator or real customer attendance, inventory and
purchasing history was obtained. Proposed fixture quantities are synthetic, not measured
recipes.

## Gaps Addressed

The scope and initial gaps are recorded in `research_papers.md` in this directory.

* **G1 — Partially resolved:** supplier pages give concrete service lead times and
  individual delivery arrangements. They do not establish a universal restaurant
  ordering frequency. [Kespro-FAQ-2026] [Kespro-Fast-2026] [MeiraNova-Service-2026]
* **G2 — Resolved for workflow separation:** school menus, hotel breakfast choices and
  HUS meal-order documentation establish distinct demand/transaction boundaries; these
  are operator accounts rather than an industry-wide frequency sample.
  [Helsinki-Menu-2026] [Scandic-FAQ-2026] [HUS-Aromi-2026]
* **G3 — Partially resolved:** guidance identifies recipe, inventory, delivery and
  receiving inputs. Clinical suitability and actual stock still need trusted customer
  information. [SeAMK-Guide-2021] [BC-Purchasing-2015] [THL-Nutrition-2023]
* **G4 — Partially resolved:** current repository fixtures support separate transaction
  and draft evaluations. Additional attendance, lot expiry and receiving cases below
  remain design proposals, not evaluated capabilities.
* **G5 — Unresolved as project data:** operational reports and published interventions
  do not supply a representative purchase-error rate or our kitchen's demand history.
  Forecasting and field savings remain future validation. [Jyvaskyla-Review-2025]
  [Malefors2022]

## Search Strategy

Research date:14November2026. This consolidation reuses primary evidence recorded in
[commercial kitchens](../../docs/research-kitchen-commercial.md) and
[institutional kitchens](../../docs/research-kitchen-institutional.md). To provide an
exact reproducible query trail, eight additional web queries were executed for this
artifact. The count covers those eight queries only, not earlier exploratory searches in
the linked documents. Target publications were2021–2026 and current operator pages;
older official or professional education sources were retained where the workflow
remained relevant, with age and country explicit. General web search was used with
domain-specific operators; no claim is made that Google Scholar, an academic aggregator
or a proprietary customer system was searched. Selection favoured originating
suppliers/operators, public institutions and university-hosted guidance. Anonymous
anecdotes, SEO articles and uninspected thesis leads were excluded as evidence.
Marketing service descriptions were labelled as such, not treated as measured
reliability. Search misses were followed by direct opening of already identified primary
URLs, including Meira Nova and Ruokavirasto receiving guidance.

Exact queries executed through web search:

1. `site.kespro.com/usein-kysyttya tilaus toimitus sovitaan asiakkaan`
2. `site.kespro.com/palvelut/ravintolatukku "tilaukset" "klo"`
3. `site.tilaajainfo.meiranova.fi/meira-nova "48"`
4. `site.scandichotels.com/en/customer-service/faq breakfast optional`
5. `site.palvelukeskus.hel.fi/fi/ruokapalvelut/kouluruokailu/vegaaniruokalista-kouluissa "kuuden viikon"`
6. `site.aromi.hus.fi/AromiSales "annostilauspohja" "aukioloaika"`
7. `site.ruokavirasto.fi "vastaanotto" "tilaus" "määrä"`
8. `site.kilport.fi/procurements_preview/zjVjtgK "korvaavaan" "toimitusrytmi"`

## Key Findings

### Delivery lead time does not establish ordering frequency

Kespro's non-peer-reviewed supplier FAQ says delivery/order times are agreed per
customer. Its Ravintolatukku service page describes regional15:00/16:00 cutoffs for
next-day supply, plus capital-region possibilities. These are service descriptions, not
proof every restaurant orders daily. [Kespro-FAQ-2026] [Kespro-Fast-2026] Meira Nova's
own description states48hours from order to delivery; interpreting this as every
customer's ordering frequency would be an unsupported inference.
[MeiraNova-Service-2026]

A historical Eurajoki municipal tender separately scores delivery, quantity and
freshness criteria by product group and requires customer acceptance before
substitutions. A tender is a contractual requirement, not evidence of the awarded
supplier's actual performance. Its terms are not Valio defaults. [Eurajoki-Tender-2021]

### Demand starts with service, not a single product query

Helsinki's non-peer-reviewed operator menu publishes a six-week school cycle, with
possible school-specific changes. A known menu horizon does not establish the wholesaler
ordering calendar. [Helsinki-Menu-2026] Scandic's non-peer-reviewed operator FAQ permits
different breakfast choices, so our inference is that occupied rooms alone cannot
determine breakfast portions. It does not publish a forecasting formula.
[Scandic-FAQ-2026]

The Finnish SeAMK/Vaasa operational guide recommends tested recipes, demand records,
inventory and service-kitchen orders, including absences. This is guidance, not observed
compliance at every site. [SeAMK-Guide-2021] Canada's professional education chapter
links portion charts, stock and package rounding; it is not Finnish law or measured
Finnish ordering cadence. [BC-Purchasing-2015]

### Hospital meal orders and wholesale ingredient orders differ

The HUS-hosted non-peer-reviewed Aromi manual describes customer permissions, reusable
meal/diet quantity templates and editing while an order window remains open. This
supports ward/customer-to-kitchen meal workflows, not HUS ingredient procurement
frequency. [HUS-Aromi-2026] Finnish national clinical guidance distinguishes diet,
portion size and texture; a matching allergen filter alone cannot demonstrate clinical
recipe equivalence. These are institution/specialist decisions, not model prescriptions.
[THL-Nutrition-2023]

### Commit, delivery and physical acceptance are separate evidence

Finnish regulator receiving guidance calls for assigned responsibility and checking
quantity, condition, dates and temperature. A placed order is not an inventory receipt.
[Ruokavirasto-Receiving-2023] Luke's public operational advice recommends stock checks
and waste/demand monitoring; its recommendation is not a transaction-frequency survey.
[Luke-Staff-2026] Jyväskylä's municipal2025 report documents special-diet
handling/serving errors and subsequent process changes, without a denominator from which
to estimate an error rate. [Jyvaskyla-Review-2025]

The already catalogued peer-reviewed Swedish study tested four interventions in two
canteens each with seven reference canteens; references also improved. It motivates
attendance-aware planning but supplies neither our demand data nor a guarantee of
savings. Only indexed abstract evidence was available in the earlier session; direct PDF
fetching timed out. [Malefors2022]

## Methodology Insights

Treat the following as engineering recommendations and testable hypotheses, not
validated benefits of this project. **Best practice:** separate ordered, delivered,
accepted and usable stock; receiving guidance supplies the reason to retain these
different facts. [Ruokavirasto-Receiving-2023] **Best practice:** configure
customer/product order windows externally instead of guessing a weekly calendar from a
supplier's fastest advertised service. [Kespro-FAQ-2026] **Best practice:** keep trusted
recipe/cohort/stock facts distinct from model-generated assumptions. [SeAMK-Guide-2021]

**Hypothesis H1:** explicit attendance, recipe and usable-stock inputs reduce
gross-versus-net quantity errors compared with a dish-name-only request. **Hypothesis
H2:** a dated shared inventory ledger reduces repeated subtraction of the same stock
across weekly meals. **Hypothesis H3:** returning two lawful trade-off baskets improves
useful human decisions over presenting only one approval button. These hypotheses
require paired/versioned tests; no result is claimed here. Score utility,
clarification/approval, committed-state safety, coverage and resource use separately.
Use negative controls: no-op, gross quantities, duplicate reservation and incorrect
delivery date. A model refusing everything must not obtain a perfect useful-purchasing
score. Never ask a judge model to make clinical dietary rules that the fixture does not
provide.

## Discovered Papers

**Zero new papers are proposed for download in this operator-focused consolidation.**
The cited Malefors2022 paper matches the title/authors/year and
DOI10.1016/j.resconrec.2021.105997 already listed in
[the existing project reading list](../real-procurement/research_internet.md). The eight
new queries targeted workflow documentation, not a new systematic literature review.
Search-result thesis leads were not read or claimed as evidence. There is no managed ARF
paper-asset catalogue in this project; no aggregator stage or paper downloads are
claimed. This does not assert that the wider literature contains no additional relevant
papers. Earlier unassessed leads remain potential follow-up work.

## Recommendations for This Task

1. Keep the implemented42-case transaction suite separate from the3-case menu draft
   suite. These counts were checked in `bench/README.md`, `bench/menu/*.json` and loader
   source at preparation; they describe local implementation, not published customer
   performance. A draft pass does not establish multi-ingredient purchase completion or
   recipe safety.
2. Use customer-supplied recipes, portions, inventory and dates for exact quantity
   oracles. Without those facts, evaluate uncertainty and mandatory review rather than
   an invented “correct standard recipe”. [SeAMK-Guide-2021]
3. Add the planned stress cases below as a versioned discovery set before promoting
   fixed unseen cases to holdout. Preserve previous reports and distinguish
   replay/regrade from new provider calls. Do not turn a disputed oracle into a better
   model score silently.
4. Request real aggregate attendance, stock/expiry, outstanding orders, receipt and
   waste history before claiming a forecast or quantified savings. The earlier paper
   corpus motivates this collection but does not supply those customer-specific data.
   [Malefors2022]

## Planned Cases and Current Boundaries

The following are **synthetic blueprints**, not implemented cases or measured operator
failures. Their factual inputs must be visible to the agent, with
source→workflow→fixture→ oracle provenance. Additional details are in the two linked
workflow documents.

| Proposed case | Supplied synthetic facts | Expected check / current gap |
| --- | --- | --- |
| School trip changes attendance | 1000expected pupils; explicit notice120away on one day; approved recipe and diet cohorts | Change only that day's needs; no invented portion; attendance revisions not a current end-to-end purchasing capability |
| Hotel breakfast plus banquet | Separate confirmed meal counts, event reserve and an outstanding supplier order | Do not infer covers from room occupancy or spend event-reserved stock; shared demand/reservation model still needed |
| Chilled versus dry inventory | Two meal dates; one chilled lot usable only on the first; dry stock remains usable | Subtract usable stock once, retain date buckets and whole packs; lot-aware planning is future work |
| Hospital texture substitution | Institution-approved diet/texture constraints and a replacement lacking compatibility evidence | Escalate to the authorised specialist; an allergen match cannot certify texture suitability |
| Supplier partial receipt after cutoff | 60Lordered,48Laccepted; explicit customer change deadline and no cancellation acknowledgement | Credit48Lonly; communicate residual deficit and committed cost; receiving/cancellation interfaces not implemented |

The original implemented text-menu cases are `school-makaronilaatikko-1000-recipe-v1`,
`school-makaronilaatikko-1000-unspecified-v1` and `restaurant-weekly-dates-stock-v1`.
They grade review-only text drafts with explicit fixture facts; they are not observed
school recipes and do not execute purchases. The42 transaction cases include5new
human-choice cases; these verify proposals and simulated human decisions separately. The
separate `bench/menu/image-v1` slice is not included in those three text cases. Image
capability must be described using its actual scored fixture/run coverage, not inferred
from a model having vision support. Check current fixture/version metadata before
quoting these counts.

## Source Index

### [Kespro-FAQ-2026]

* **Type**: documentation
* **Title**: Usein kysyttyä
* **Author/Org**: Kespro
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://www.kespro.com/usein-kysyttya
* **Peer-reviewed**: no
* **Relevance**: Undated supplier HTML; read on access date. Customer-specific
  ordering/delivery times, not measured ordering frequency.

### [Kespro-Fast-2026]

* **Type**: documentation
* **Title**: Kespro Ravintolatukku
* **Author/Org**: Kespro
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://www.kespro.com/palvelut/ravintolatukku
* **Peer-reviewed**: no
* **Relevance**: Undated supplier service HTML; read. Regional cutoffs and advertised
  next-day/capital-region possibilities, not independently measured reliability.

### [MeiraNova-Service-2026]

* **Type**: documentation
* **Title**: Meira Nova: yhdellä tilauksella kaikki sovittuun aikaan
* **Author/Org**: Meira Nova
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://tilaajainfo.meiranova.fi/meira-nova
* **Peer-reviewed**: no
* **Relevance**: Undated supplier HTML; read. Describes48-hour order-to-delivery
  service, not how frequently each kitchen orders.

### [Scandic-FAQ-2026]

* **Type**: documentation
* **Title**: Frequently asked questions: breakfast flexibility
* **Author/Org**: Scandic Hotels
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://www.scandichotels.com/en/customer-service/faq
* **Peer-reviewed**: no
* **Relevance**: Undated operator HTML; read. Optional breakfast provides grounds for
  distinguishing room occupancy and breakfast covers; not an operator forecast.

### [Helsinki-Menu-2026]

* **Type**: documentation
* **Title**: Koulujen vegaaniruokalista
* **Author/Org**: Palvelukeskus Helsinki
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**:
  https://palvelukeskus.hel.fi/fi/ruokapalvelut/kouluruokailu/vegaaniruokalista-kouluissa
* **Peer-reviewed**: no
* **Relevance**: Autumn2026 operator menu HTML; read. Six-week cycle and local changes,
  not a wholesale ordering calendar.

### [HUS-Aromi-2026]

* **Type**: documentation
* **Title**: Aromin käyttöohje
* **Author/Org**: HUS-hosted AromiSales
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://aromi.hus.fi/AromiSales/FI/Default/HelpPrint.aspx
* **Peer-reviewed**: no
* **Relevance**: Undated public manual HTML; read. Customer permissions and meal/diet
  ordering windows; wholesale procurement cadence not established.

### [SeAMK-Guide-2021]

* **Type**: documentation
* **Title**: Opas ruokahävikin vähentämiseen ammattikeittiössä
* **Author/Org**: SeAMK and University of Vaasa, Wasteless
* **Date**: 2021-02-08
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**:
  https://storage.googleapis.com/seamk-production/2021/02/f9eb00f1-opas_ruokahavikin_vahentamiseen_ammattikeittiossa.pdf
* **Peer-reviewed**: no
* **Relevance**: PDF foreword date; operational sections pp12–13 read in institutional
  pass. Tested recipes, inventory, attendance and agreed order/change windows; guidance
  not adoption statistics.

### [BC-Purchasing-2015]

* **Type**: documentation
* **Title**: Basic Kitchen and Food Service Management: Purchasing
* **Author/Org**: BC Cook Articulation Committee / BCcampus
* **Date**: 2015-09-04
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**:
  https://opentextbc.ca/basickitchenandfoodservicemanagement/chapter/purchasing/
* **Peer-reviewed**: no
* **Relevance**: Canadian professional textbook HTML; read. Portion charts, stock, pack
  rounding and perishability; not Finnish law or frequency measurements.

### [Eurajoki-Tender-2021]

* **Type**: documentation
* **Title**: Eurajoen kunnan elintarvikehankinta2022–2024
* **Author/Org**: Municipality of Eurajoki via Kilport
* **Date**: 2021-06-04
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://kilport.fi/procurements_preview/zjVjtgK
* **Peer-reviewed**: no
* **Relevance**: Public municipal tender text §3.3/5.1 read. Requested product-group
  service levels and prior substitution consent, not actual fulfilment or current Valio
  terms.

### [Ruokavirasto-Receiving-2023]

* **Type**: documentation
* **Title**: Vastaanotto
* **Author/Org**: Finnish Food Authority / Ruokavirasto
* **Date**: 2023-09-05
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**:
  https://www.ruokavirasto.fi/elintarvikkeet/elintarvikeala/hygieeninen-toiminta/tuotanto--ja-kasittelyhygienia/vastaanotto/
* **Peer-reviewed**: no
* **Relevance**: Regulator HTML with update date; read. Physical receiving checks and
  records, not transaction-error prevalence.

### [THL-Nutrition-2023]

* **Type**: documentation
* **Title**: Ravitsemushoitosuositus, THL Ohjaus3/2023
* **Author/Org**: VRN and Finnish Institute for Health and Welfare
* **Date**: 2023-03
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**:
  https://www.julkari.fi/bitstream/handle/10024/146233/URN_ISBN_978-952-343-977-1.pdf
* **Peer-reviewed**: no
* **Relevance**: National clinical food-service guidance PDF; relevant roles/texture
  sections read in institutional pass. Does not authorise agent diagnosis or clinical
  substitution.

### [Jyvaskyla-Review-2025]

* **Type**: documentation
* **Title**: Jyväskylän kaupungin neljännesvuosikatsaus9/2025
* **Author/Org**: City of Jyväskylä
* **Date**: 2025-09
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**:
  https://www.jyvaskyla.fi/sites/default/files/2025-11/neljannesvuosikatsaus_9_2025.pdf
* **Peer-reviewed**: no
* **Relevance**: Reporting period1–9/2025; publication PDF under November path, exact
  release day not established. Relevant risk/process slides read; no error denominator.

### [Luke-Staff-2026]

* **Type**: documentation
* **Title**: Henkilökunnalle: ruokahävikin vähentäminen
* **Author/Org**: Natural Resources Institute Finland / Luke
* **Date**: 2026-11-14
* **Accessed**: 2026-11-14
* **Date meaning**: Publication/update/reporting date where established; otherwise
  access date, not an invented publication date.
* **URL**: https://projects.luke.fi/ravintolafoorumi/henkilokunnalle/
* **Peer-reviewed**: no
* **Relevance**: Undated operational advice HTML; read in commercial pass. Stock checks
  and production/waste monitoring recommendations, not a current adoption survey.

### [Malefors2022]

* **Type**: paper
* **Title**: Testing interventions to reduce food waste in school catering
* **Authors**: Christopher Malefors, Niina Sundin, Malou Tromp, Mattias Eriksson
* **Year**: 2022
* **DOI**: 10.1016/j.resconrec.2021.105997
* **URL**: https://pub.epsilon.slu.se/25883/1/malefors_c_et_al_211020.pdf
* **Peer-reviewed**: yes
* **Accessed**: 2026-11-14
* **Relevance**: Swedish school intervention study already in the project reading list.
  Indexed abstract read in the earlier pass; direct full-PDF retrieval timed out.
  Supports study motivation, not this project's forecast accuracy or universal savings.
