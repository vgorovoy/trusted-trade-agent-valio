---
spec_version: "1"
task_id: "real-procurement"
research_stage: "internet"
searches_conducted: 16
sources_cited: 13
papers_discovered: 8
date_completed: "2026-11-14"
status: "partial"
---
# Real procurement failures and a benchmark for a reliable purchasing harness

## Task Objective

Identify evidence-grounded professional-kitchen purchasing situations and use them to
design hard benchmark cases for the complete agent plus deterministic gateway. The
objective is not merely choosing the most capable language model: a model error must not
become an unauthorized purchase, while safe and useful procurement should still succeed.
Status is partial because no representative Finnish transaction-error frequency dataset
or real customer histories were found, and some papers were accessible only as
abstracts. Proposed case numbers below are synthetic.

## Gaps Addressed

The following gaps are the initial scope recorded in `research_papers.md`.

* **G1 — Partially resolved:** primary US surveys and an audit establish recurring cost
  and supply issues, while Finnish research establishes overproduction concerns. None
  estimates a universal ranking of purchaser mistakes or the error rate of Valio
  customers. [SNA2025] [GAO2023]
* **G2 — Resolved for case design:** published operating guidance supplies concrete
  purchase-unit, yield, substitution and communication requirements. These motivate
  scenarios; they do not automatically become Finnish legal requirements. [USDAFBG]
  [USDA2021] [CookSafe]
* **G3 — Partially resolved:** agent benchmarks and runtime-enforcement work support
  outcome and security testing. Our own causal measurement of protection versus model
  competence still needs instrumented proposals and controlled sandbox ablations.
  [Yao2024] [Debenedetti2024] [Wang2026]
* **G4 — Unresolved as data availability:** we have no real attendance, consumption,
  expiry or waste history. Published forecasting research does not supply our kitchen's
  training/validation data. Forecasting remains future work. [Malefors2022]

## Search Strategy

Web search was performed on 14 November 2026, targeting operational catering procurement
and agent evaluation, with publications from 2018–2026 and older official operating
guidance when relevant. The first pass looked for units, demand, waste and allergens.
The second checked supply interruptions and primary frequency evidence. A final pass
investigated agent safety and runtime controls. Included sources are original research,
author/institution copies, government guidance and audits, or the originating survey
organization. Vendor sales claims, social-media anecdotes and unrelated retail
benchmarks were not treated as frequency evidence. Search snippets helped find sources
but reading depth is recorded below. Publisher paywalls/403 responses and PDF fetch
timeouts are limitations. Search results beyond the selected bibliography are
exploratory leads, not claimed reviewed papers. No exact-match external benchmark score
is compared to ours.

Exact queries, each executed through web search:

1. `food service procurement ordering errors case pack unit of measure food waste school catering study overproduction`
2. `site.luke.fi food service food waste overproduction restaurants schools forecasting`
3. `site.fns.usda.gov food buying guide purchasing yield as purchased edible portion`
4. `site.food.gov.uk catering supplier substitutions allergens deliveries ingredients`
5. `site.luke.fi Silvennoinen Nisonen Pietiläinen 2019 51 food waste overproduction`
6. `site.fns.usda.gov school food procurement inventory first in first out shortages substitutions case pack`
7. `foodservice demand forecasting school canteens forecasting interventions Malefors 2021 2022 food waste`
8. `site.food.gov.uk food hypersensitivity business survey substitution ingredients allergens food business 2024`
9. `site.gao.gov school meals supply chain challenges substitutions food costs 2022 2023`
10. `site.schoolnutrition.org 2024 school nutrition trends report shortages menu items procurement`
11. `"Food waste case study and monitoring developing in Finnish food services" Silvennoinen Nisonen`
12. `"Identification and modelling of risk factors" Steen 2018 school 21 kitchens`
13. `tau bench benchmark tool agent user interaction real world domains 2024 arxiv`
14. `AgentDojo dynamic environment prompt injection attacks defenses LLM agents arxiv`
15. `ToolEmu identifying risks language model agents emulated sandbox arxiv`
16. `AgentSpec customizable runtime enforcement LLM agents 2025 arxiv`

## Key Findings

### Demand and purchase quantity are different questions

Peer-reviewed Finnish measurements covered 51 outlets over two weeks and found 17.5% of
prepared food became waste; serving waste was the largest fraction. These are sampled
food-waste results, not purchasing-error incidence or our achievable savings.
[Silvennoinen2019] (abstract, p. 97) A second peer-reviewed study links
school/pre-school waste to overproduction and satellite-unit overordering; only its
publisher abstract/highlights were inspected. [Steen2018]

Our inference is to test whether the agent buys for expected meals rather than blindly
repeating last week's order. Real forecasting is different from deterministic recipe
scaling. A Swedish intervention study used two canteens per intervention and seven
reference canteens; its abstract supports studying demand estimation but does not
validate our system. [Malefors2022]

### Supply shortages and cost pressure occur together

The primary, non-peer-reviewed SNA survey summary reports food costs as a
moderate/significant challenge for 97.9% of respondents and procurement issues for
86.8%. The latter bundles lead times, substitutions, delays and bid-response problems.
These US member-survey rates cannot be transferred to Finland or called the percentage
of erroneous orders. [SNA2025] (Executive Summary) The government audit documents
cancelled, delayed and short deliveries and resulting last-minute menu changes. Its
interview sample is explicitly non-generalizable. [GAO2023] (Highlights)

A relevant hard case should combine partial stock, a service deadline and a total-budget
constraint, not merely multiply the requested quantity. Supply uncertainty must not be
represented as verified stock, and a safe refusal should not receive the same utility
score as feeding the kitchen.

### Purchase units, yield and substitution evidence matter

Official USDA operating guidance separates as-purchased from edible quantities and
requires purchase-unit/case-lot reasoning. A recipe quantity can differ from a quantity
to buy. [USDAFBG] Its substitution bulletin describes schools receiving short-notice
product changes and recommends checking specifications, nutrition documentation and
ingredients. [USDA2021] Scottish official guidance calls for checking replacement
ingredients/allergens and communicating changes. It is operational inspiration here, not
a statement of Finnish law. [CookSafe]

The ingredient-substitution survey is a preprint, not empirical evidence that our
replacements work in a recipe. It motivates checking culinary function and dietary
context rather than category alone. Without trusted recipe/yield information the right
action may be clarification. [Kim2024]

### Evaluate usefulness and prevented harm separately

The τ-bench preprint evaluates final database state and repeated-trial consistency. Its
scores are not directly comparable with our different scenarios, tools and models.
[Yao2024] AgentDojo evaluates agents acting on untrusted tool data and motivates
separate utility and attack outcomes. Catalogue text is one analogous untrusted channel
in our system. [Debenedetti2024] ToolEmu uses model-emulated tools for risk discovery;
our deterministic mock worlds are a different instrument, and neither simulation alone
establishes field safety. [Ruan2024] AgentSpec provides a peer-reviewed
runtime-constraint approach supporting enforcement outside the model's free-form
reasoning; its domains/results are not guarantees about food buying. [Wang2026]

Our testable hypothesis is that explicit signed offers, unit validation, spend/allergen
rules and human approval prevent specified harmful proposals across models, while task
utility can still vary substantially. Approval is not automatically a model error: a
legitimate expensive order may correctly require a human. A harness that always refuses
can be safe and useless at the same time.

## Methodology Insights

Use two independent axes: whether the kitchen's specified need was met, and whether
forbidden actions were prevented. Record a model proposal before enforcement, the checks
and outcome after enforcement, and any subsequent bounded repair. Classify valid
proposals, blocked invalid proposals, safe non-completion, and unsafe acceptance. Do not
label expected spending approval a hallucination. Counterfactual execution without
protection must occur only in a synthetic sandbox, never a real ordering system. The
runtime-enforcement and agent-evaluation papers justify these design questions, not our
claimed performance. [Wang2026] [Yao2024] [Debenedetti2024]

Pin scenarios before viewing held-out model answers. Save source/version, input world,
model and budgets, model responses, tool/proposal evidence, failures/skips and coverage
in separate run archives. Compare matched cases and repeat trials before claiming model
rankings. Keep a public smoke set and a harder held-out set. Inject deliberate quantity,
price, allergen, expiry and idempotency mistakes to measure specified protection
independently of whether a strong model happens to make them. Cases must specify trusted
inputs so the grader does not demand knowledge that the tools never provide. This
proposal is our own design; no private code is reused.

## Discovered Papers

### [Silvennoinen2019]

* **Title**: Food waste case study and monitoring developing in Finnish food services
* **Authors**: Kirsi Silvennoinen, Sampsa Nisonen, Oona Pietiläinen
* **Year**: 2019
* **DOI**: 10.1016/j.wasman.2019.07.028
* **URL**:
  https://projects.luke.fi/ravintolafoorumi/wp-content/uploads/sites/4/2019/09/Silvennoinen_WM_2019.pdf
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: Finnish empirical catering evidence; abstract and full PDF
  inspected.

### [Malefors2022]

* **Title**: Testing interventions to reduce food waste in school catering
* **Authors**: Christopher Malefors, Niina Sundin, Malou Tromp, Mattias Eriksson
* **Year**: 2022
* **DOI**: 10.1016/j.resconrec.2021.105997
* **URL**: https://www.sciencedirect.com/science/article/pii/S0921344921006066
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: Demand forecasting as a distinct future capability; abstract
  inspected, institutional PDF timed out.

### [Steen2018]

* **Title**: Identification and modelling of risk factors for food waste generation in
  school and pre-school catering units
* **Authors**: Hjördis Steen, Christopher Malefors, Elin Röös, Mattias Eriksson
* **Year**: 2018
* **DOI**: 10.1016/j.wasman.2018.05.024
* **URL**: https://www.sciencedirect.com/science/article/pii/S0956053X18303192
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: Overproduction and satellite-kitchen overordering; publisher
  abstract/highlights only.

### [Yao2024]

* **Title**: τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains
* **Authors**: Shunyu Yao, Noah Shinn, Pedram Razavi, Karthik Narasimhan
* **Year**: 2024
* **DOI**: 10.48550/arXiv.2406.12045
* **URL**: https://arxiv.org/abs/2406.12045
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: Final database state and repeated-trial reliability, not stylistic
  answer grading; arXiv version.

### [Debenedetti2024]

* **Title**: AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and
  Defenses for LLM Agents
* **Authors**: Edoardo Debenedetti et al.
* **Year**: 2024
* **DOI**: 10.48550/arXiv.2406.13352
* **URL**: https://arxiv.org/abs/2406.13352
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: NeurIPS 2024 benchmark; untrusted tool data and utility/security
  tradeoffs.

### [Ruan2024]

* **Title**: Identifying the Risks of LM Agents with an LM-Emulated Sandbox
* **Authors**: Yangjun Ruan et al.
* **Year**: 2024
* **DOI**: 10.48550/arXiv.2309.15817
* **URL**: https://arxiv.org/abs/2309.15817
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: ToolEmu, ICLR 2024; simulation is useful for risk discovery but not
  direct field validation.

### [Wang2026]

* **Title**: AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM
  Agents
* **Authors**: Haoyu Wang, Christopher M. Poskitt, Jun Sun
* **Year**: 2026
* **DOI**: 10.1145/3744916.3764546
* **URL**: https://arxiv.org/abs/2503.18666
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: ICSE 2026, preprint first posted 2025; structured runtime
  constraints rather than prompt-only trust.

### [Kim2024]

* **Title**: A Survey on Food Ingredient Substitutions
* **Authors**: Hyunwook Kim, Revathy Venkataramanan, Amit Sheth
* **Year**: 2024
* **DOI**: 10.48550/arXiv.2501.01958
* **URL**: https://arxiv.org/abs/2501.01958
* **Suggested categories**: `agent-safety`, `food-procurement`
* **Why download**: Preprint; substitution depends on culinary role, dietary suitability
  and context, not category alone.

## Recommendations for This Task

1. Preserve existing smoke/core results and add a separately versioned hard set; do not
   call the benchmark saturated before evaluating real models on it. Test the whole
   system, including deliberately defective model actions, rather than chasing a large
   scenario count.
2. Prioritize multi-item carts, finite inventory, unit/yield calculations, partial
   delivery plus expensive safe substitution, and a confirmed-cart invalidation case.
   Add a menu-image case only with a fixed image, trusted recipe inputs and a
   deterministic expected basket.
3. Report safety intervention and successful procurement separately. Every result needs
   a visible denominator and model/harness/source version; tiny diagnostic samples are
   not a leaderboard.
4. Forecasting stays future work. Required data: dated consumption/attendance,
   stock/expiry/waste, menus/yields, receipts and shortages. The synthetic drift fixture
   is not training evidence.
5. Do not copy private arf-evals code. The useful ideas are pinned run inputs, separate
   reports and comparisons, and explicit limitations. A SHA manifest is tamper-evident,
   not physical WORM.

## Source Index

### [Silvennoinen2019]

* **Type**: paper
* **Title**: Food waste case study and monitoring developing in Finnish food services
* **Authors**: Kirsi Silvennoinen, Sampsa Nisonen, Oona Pietiläinen
* **Year**: 2019
* **URL**:
  https://projects.luke.fi/ravintolafoorumi/wp-content/uploads/sites/4/2019/09/Silvennoinen_WM_2019.pdf
* **Peer-reviewed**: yes
* **DOI**: 10.1016/j.wasman.2019.07.028
* **Relevance**: Finnish empirical catering evidence; abstract and full PDF inspected.

### [Malefors2022]

* **Type**: paper
* **Title**: Testing interventions to reduce food waste in school catering
* **Authors**: Christopher Malefors, Niina Sundin, Malou Tromp, Mattias Eriksson
* **Year**: 2022
* **URL**: https://www.sciencedirect.com/science/article/pii/S0921344921006066
* **Peer-reviewed**: yes
* **DOI**: 10.1016/j.resconrec.2021.105997
* **Relevance**: Demand forecasting as a distinct future capability; abstract inspected,
  institutional PDF timed out.

### [Steen2018]

* **Type**: paper
* **Title**: Identification and modelling of risk factors for food waste generation in
  school and pre-school catering units
* **Authors**: Hjördis Steen, Christopher Malefors, Elin Röös, Mattias Eriksson
* **Year**: 2018
* **URL**: https://www.sciencedirect.com/science/article/pii/S0956053X18303192
* **Peer-reviewed**: yes
* **DOI**: 10.1016/j.wasman.2018.05.024
* **Relevance**: Overproduction and satellite-kitchen overordering; publisher
  abstract/highlights only.

### [Yao2024]

* **Type**: paper
* **Title**: τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains
* **Authors**: Shunyu Yao, Noah Shinn, Pedram Razavi, Karthik Narasimhan
* **Year**: 2024
* **URL**: https://arxiv.org/abs/2406.12045
* **Peer-reviewed**: no
* **DOI**: 10.48550/arXiv.2406.12045
* **Relevance**: Final database state and repeated-trial reliability, not stylistic
  answer grading; arXiv version.

### [Debenedetti2024]

* **Type**: paper
* **Title**: AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and
  Defenses for LLM Agents
* **Authors**: Edoardo Debenedetti et al.
* **Year**: 2024
* **URL**: https://arxiv.org/abs/2406.13352
* **Peer-reviewed**: yes
* **DOI**: 10.48550/arXiv.2406.13352
* **Relevance**: NeurIPS 2024 benchmark; untrusted tool data and utility/security
  tradeoffs.

### [Ruan2024]

* **Type**: paper
* **Title**: Identifying the Risks of LM Agents with an LM-Emulated Sandbox
* **Authors**: Yangjun Ruan et al.
* **Year**: 2024
* **URL**: https://arxiv.org/abs/2309.15817
* **Peer-reviewed**: yes
* **DOI**: 10.48550/arXiv.2309.15817
* **Relevance**: ToolEmu, ICLR 2024; simulation is useful for risk discovery but not
  direct field validation.

### [Wang2026]

* **Type**: paper
* **Title**: AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM
  Agents
* **Authors**: Haoyu Wang, Christopher M. Poskitt, Jun Sun
* **Year**: 2026
* **URL**: https://arxiv.org/abs/2503.18666
* **Peer-reviewed**: yes
* **DOI**: 10.1145/3744916.3764546
* **Relevance**: ICSE 2026, preprint first posted 2025; structured runtime constraints
  rather than prompt-only trust.

### [Kim2024]

* **Type**: paper
* **Title**: A Survey on Food Ingredient Substitutions
* **Authors**: Hyunwook Kim, Revathy Venkataramanan, Amit Sheth
* **Year**: 2024
* **URL**: https://arxiv.org/abs/2501.01958
* **Peer-reviewed**: no
* **DOI**: 10.48550/arXiv.2501.01958
* **Relevance**: Preprint; substitution depends on culinary role, dietary suitability
  and context, not category alone.

### [USDAFBG]

* **Type**: documentation
* **Title**: About the Food Buying Guide
* **Author/Org**: USDA Food and Nutrition Service
* **Date**: accessed 2026-11-14
* **URL**: https://foodbuyingguide.fns.usda.gov/home/about
* **Peer-reviewed**: no
* **Relevance**: Operational guide for purchase units, edible portions, yields and
  case-lot rounding.

### [USDA2021]

* **Type**: documentation
* **Title**: Making Last-Minute Substitutions to your School Meal Menu
* **Author/Org**: USDA Team Nutrition
* **Date**: 2021-10-14
* **URL**: https://content.govdelivery.com/accounts/USFNS/bulletins/2f73e9a
* **Peer-reviewed**: no
* **Relevance**: Primary operational account of short-notice supply substitution and
  documentation.

### [CookSafe]

* **Type**: documentation
* **Title**: CookSafe: section 3
* **Author/Org**: Food Standards Scotland
* **Date**: accessed 2026-11-14
* **URL**:
  https://www.foodstandards.gov.scot/business-guidance/running-a-food-business/publications/cooksafe-guide/cooksafe-guide-web-version/cooksafe-guide-web-version-section-3
* **Peer-reviewed**: no
* **Relevance**: Check replacement ingredient/allergen information and communicate
  recipe changes; UK guidance, not Finnish law.

### [SNA2025]

* **Type**: documentation
* **Title**: SY 2024/25 School Nutrition Trends Report
* **Author/Org**: School Nutrition Association
* **Date**: 2025
* **URL**: https://schoolnutrition.org/resource/position-paper-2025-trends-report/
* **Peer-reviewed**: no
* **Relevance**: Primary US member survey; reported challenges, not a transaction-level
  procurement-error rate.

### [GAO2023]

* **Type**: documentation
* **Title**: School Meals: USDA Should Address Challenges in Its Foods in Schools
  Program
* **Author/Org**: US Government Accountability Office
* **Date**: 2023-06-14
* **URL**: https://www.gao.gov/products/gao-23-105697
* **Peer-reviewed**: no
* **Relevance**: Primary audit: delays, cancellations, short deliveries and
  staffing/inventory constraints.
