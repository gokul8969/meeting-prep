# PRD: Meeting Prep Agent

**For:** amasol and klaricore sales reps
**Status:** Draft v0.2, 2026-11-24
**Working name:** Meeting Prep Agent (final name to be decided)

---

## 1. Summary

A chat-based research agent that gets sales reps ready for customer meetings in minutes. The rep creates a customer, opens that customer's chat, puts in everything they know, and tells the agent to go. The agent researches the company and the attendees from many angles at once (web, Apollo, Treg). It works out what matters for what we sell, keeps facts apart from guesses, and turns the guesses into questions. It then returns a short brief.

After the meeting, the rep tells the agent what they learned in the same chat. That goes into the customer's record, so the next meeting with that customer starts from everything already known.

> Help our reps prepare for any customer meeting in minutes, and remember everything we learn about each customer so every meeting starts smarter than the last.

---

## 2. Problem

Sales reps at amasol and klaricore meet large companies to sell IT services. Before each meeting they need to know:
- what the company does
- what IT it runs
- what has changed lately
- who they are meeting
- what to ask

Today they research this by hand. It takes hours. What they learn is forgotten after the meeting, so the next meeting with the same customer starts from zero.

---

## 3. Goals and non-goals

### Goals
- G1. A rep gets a useful meeting brief in **minutes** instead of hours.
- G2. The brief is **about what we sell**, not generic company facts.
- G3. Every statement in the brief is **marked as confirmed, likely or unknown**. Confirmed means it has a source the rep can open.
- G4. Guesses become **questions the rep can ask** in the meeting.
- G5. Knowledge **adds up**: each meeting with a customer starts from the full record and only looks for what is new.
- G6. For important customers, reps hear about **big changes** without a meeting being booked.

### Non-goals (v1)
- Writing outreach emails or sequences.
- Updating CRM pipeline stages, forecasts or deal values.
- Recording or transcribing meetings.
- Writing proposals or decks.
- Finding new prospects (this is about prepping for a meeting already planned).

---

## 4. Users

| User | Need |
|---|---|
| **Sales rep** (amasol, klaricore) | Prepare fast, walk in knowing the customer and the people, know what to ask |
| **Account owner** for important customers | Hear about big changes in their accounts |
| **Sales lead / manager** | See that reps are prepared; reuse what the team knows about a customer |
| **Admin** | Manage users, data source keys, the offerings catalog, cost limits |

---

## 5. Product shape

An **agent workspace**, used like a chat with Claude:

- A list of **customers**. Each customer has:
  - **one chat** with the agent, kept for good;
  - the **customer record**, everything known, each fact with its source and date;
  - the **briefs**, one per meeting;
  - the **people**, the attendees met so far and what we know about each.
- The rep works mainly in the chat. The agent asks, researches, shows progress, gives the brief, and takes follow-up requests and post-meeting notes, all in the same conversation.

---

## 6. User journey

### 6.1 First meeting with a new customer
1. **Create customer.** The rep enters the company name and, optionally, the website. The agent confirms it is the right company (for example "Siemens AG, Munich, not Siemens Healthineers?").
2. **Open the chat and put everything in.** The rep pastes or types whatever they have, in any form:
   - who will be in the room (names, titles, LinkedIn links, email signatures)
   - what the meeting is for and what a good outcome looks like
   - anything already known: past emails, notes, documents
3. **The agent sums it up and asks what is missing.** It repeats back what it understood, in short form, and asks only for what it truly needs (for example "What would a good outcome be?" or "Is Maria Huber the CIO or the deputy?"). The rep can answer or say "go anyway".
4. **"Go."** The agent plans the research and starts it.
5. **Live progress in the chat.** For example: "Company ✓ · IT setup (3 sources) … · Attendee 2 of 3 … · Round 2: checking who the CIO is".
6. **The brief appears in the chat** and is saved to the customer.
7. **Follow-up chat.** The rep can ask for more, for example "go deeper on their cloud move", "what do we know about their monitoring tools?", "make the questions shorter", "add Tom Berger, he's joining too". The agent researches more or updates the brief.

### 6.2 After the meeting
8. The rep tells the agent what they learned, in any form: typed notes, pasted email, bullet points.
9. The agent turns this into **facts for the record**:
   - New facts are saved with the source "rep, date".
   - Guesses that the meeting answered become confirmed, are corrected, or are dropped.
   - Open questions that were answered are closed.
   - The agent shows what it will save, and the rep confirms or corrects it.
10. The agent can suggest follow-ups, for example "You said they're renewing their monitoring contract in Q2. Shall I note that as the next step?"

### 6.3 Next meeting with the same customer
11. The rep opens the same chat and says, for example, "Meeting again on 12 March with the CIO and a new person, Anna Klein".
12. The agent **starts from the record**. It only researches what is new since the last run, plus anyone new in the room.
13. The new brief shows **what has changed since last time** at the top.

### 6.4 Alerts for important customers
14. A rep or admin marks a customer as **important**.
15. The agent checks important customers on a schedule, for news, changes of leaders, takeovers, big job-posting changes and similar.
16. If something big happens, the account owner is told, and the change is posted in that customer's chat with its source.

---

## 7. Functional requirements

### 7.1 Customers and workspace
- FR-1. Create a customer by name, with an optional website. The agent resolves it to one company and the rep confirms it.
- FR-2. Each customer has one lasting chat, a record, a list of briefs and a list of people.
- FR-3. The customer list can be searched and shows the last meeting, the next meeting and whether the customer is marked important.
- FR-4. A customer belongs to amasol, klaricore or both (see open question Q2).

### 7.2 Chat and intake
- FR-5. The rep can type, paste long text, and upload files (PDF, DOCX, email files, images of business cards or slides).
- FR-6. The agent pulls out the attendees, meeting purpose, good outcome, date and known facts from whatever the rep gives.
- FR-7. Before research, the agent shows a short summary and asks only for what it is missing. It asks at most a few questions at once. The rep can always skip and say "go".
- FR-8. The agent never starts research without the rep saying go. This applies to the first run and to any run that costs much.
- FR-9. The rep can stop a research run at any time.

### 7.3 Research (stage 3)
- FR-10. **The lead agent plans the research.** From the rep's input, the record and the meeting goal, it picks the angles to research.
- FR-11. **Standard angles:**

| Angle | Looks for | Main sources |
|---|---|---|
| Company and business | What they do, size, segments, strategy, money pressure | Company site, annual report, investor pages, Apollo, Treg |
| IT setup and tools | Tools they use (monitoring, APM, cloud, ITSM, ERP), IT partners, outsourcing | Job postings, Apollo tech stack, vendor case studies, talks, tenders |
| Recent changes | Takeovers, new leaders, restructuring, cloud moves, outages, new rules they must follow | News from the last 6–12 months, press releases, Treg signals |
| Signs of trouble | Outages, security incidents, hiring rushes, cost cuts | News, status pages, job postings |
| Each attendee | Role, time in role, past jobs, talks, posts, what they likely care about | LinkedIn (through Apollo, Treg, or what the rep pastes), company site, press, talks |
| Our history | What we already know, what's open, what changed since last time | Customer record |

- FR-12. The lead agent can add **extra angles** based on the meeting goal, for example "their SAP move" or "their NIS2 programme".
- FR-13. **Each angle is handled by its own researcher sub-agent.** Researchers run at the same time, with their own context, and send back only structured findings.
- FR-14. **Every finding** contains:
  - the claim
  - its kind: fact or signal
  - the source (link or data source and record)
  - an exact quote or data field
  - the date it was published or observed
  - the angle it belongs to
- FR-15. **Gap check and second round.** After the first round, the agent lists what is missing, contradictory or out of date, and runs a second, targeted round. There are at most 2 rounds per run unless the rep asks for more.
- FR-16. **Source check.** Before a web finding can count as confirmed, the system checks in code that the quote really appears on the page it cites. Findings that fail are marked unverified.
- FR-17. **Repeat runs only look for what is new.** Researchers get the record and the date of the last run, and look only for what is newer. Findings already in the record are not repeated.
- FR-18. **Budgets.** Each run has limits on searches, pages read, Apollo and Treg calls, and cost. When a researcher hits its limit, it stops and reports what it has.
- FR-19. **Progress** is shown in the chat as researchers start and finish.

### 7.4 Making sense of it (stage 4)
- FR-20. One step takes all checked findings, the record, the meeting goal and the **offerings catalog**, and produces the brief.
- FR-21. Every statement in the brief is marked:
  - **Confirmed:** a checked primary source, two independent sources, or the rep or the record says so.
  - **Likely:** reasoned from evidence, with the reason shown ("because their job ads ask for X and Y").
  - **Unknown:** important, but not found.
- FR-22. Every likely or unknown item that matters becomes a **question to ask**.
- FR-23. Problems are **matched to our offerings** from the catalog. Each problem names its evidence and the offering that fits.
- FR-24. A check runs after the brief is written: any statement marked confirmed must point to a confirmed finding, or the brief is fixed before it is shown.
- FR-25. Every source in the brief can be clicked.

### 7.5 The brief (output)
Shown in the chat and saved to the customer. Short enough to read in **5 minutes**.

1. **What's new since last time** (repeat meetings only)
2. **Company in brief:** 5–8 lines, each marked confirmed, likely or unknown
3. **Their IT setup:** known tools and partners, with evidence
4. **Recent changes:** dated, with sources
5. **People in the room:** for each person, role, background, what they likely care about, how to approach them, and what we discussed before
6. **Likely problems we can help with:** ranked, each with evidence and the offering that fits
7. **Questions to ask:** grouped by topic and by person, each tied to the guess it checks
8. **Suggested next step to aim for:** tied to the rep's idea of a good outcome
9. **Sources**

- FR-26. The brief can be exported (format: see open question Q6).
- FR-27. The rep can edit the brief in the chat ("drop question 4", "make this shorter").

### 7.5a Using the brief in the meeting (workflow step 6)
- FR-27a. The brief reads well **on a phone**, so the rep can look at it just before or during the meeting.
- FR-27b. Each brief has a **one-page version**: the 3 key facts, the people in one line each, the top 5 questions and the next step.
- FR-27c. The questions are a **checklist** the rep can tick and add short answers to during or right after the meeting. These ticks and answers flow straight into the post-meeting notes (§6.2).
- FR-27d. The brief is available offline once opened (proposed; see open question Q6 on export).

### 7.6 Customer record
- FR-28. The record is a list of **facts**. Each fact has its text, topic (company, IT, people, changes, problems, history), source, date, status (confirmed, likely, unknown, outdated) and who added it (agent or rep).
- FR-29. Briefs are always made fresh from the record and the new research. The record, not old briefs, is the single store of what we know.
- FR-30. When sources disagree: what the rep says beats the web; newer beats older; the conflict is shown, not hidden.
- FR-31. The rep can see, edit and delete any fact.
- FR-32. **People** are kept per customer. Each person has their role history, what we know about them, and what was discussed with them.

### 7.7 Learning after the meeting
- FR-33. The agent turns the rep's notes into proposed changes to the record (new facts, confirmed guesses, corrected facts, answered questions, next steps) and shows them for the rep to confirm.
- FR-34. Nothing from the notes is saved without the rep confirming it.

### 7.8 Alerts
- FR-35. A customer can be marked important.
- FR-36. Important customers are checked on a schedule (how often: see open question Q8) for big changes only: new leaders, takeovers, restructuring, outages, big hiring or cuts in IT, large tenders.
- FR-37. Each alert names the change, why it matters to us, and its source. It is posted in the customer's chat and sent to the account owner (channel: see open question Q9).
- FR-38. Alerts don't repeat something already in the record.

### 7.9 Offerings catalog
- FR-39. The admin uploads the catalog of what amasol and klaricore sell: each offering, the problems it solves, the signals that point to it, and who usually buys it. **The catalog will be provided later.**
- FR-40. Until the catalog exists, the brief still lists likely problems but does not match them to offerings, and says so. **This weakens the "what matters for what we sell" step of the problem statement until the catalog is in.**

---

## 8. Data sources

| Source | Used for | Notes |
|---|---|---|
| **Rep input** | Attendees, meeting goal, known facts, notes after the meeting | Ranks highest |
| **Customer record** | Everything learned before | Starting point of every run |
| **Apollo** | Company data (size, revenue, industry, tech stack, headcount trends, job postings); people data (title, seniority, work history, LinkedIn profile) | Main structured source |
| **Treg** (treg.to) | Gateway to many data providers: people and company enrichment, funding, buying signals, a second source to cross-check Apollo | Priced per call. Each researcher gets a short allowed list of Treg tools, not the full catalog of about 2,600 endpoints |
| **LinkedIn** | Attendee profiles, role history, posts and activity; company page and headcount | Through Apollo, Treg and what the rep pastes |
| **Web search and page reading** | News, press releases, annual reports, investor pages, job postings, talks, vendor case studies, tenders | Every web fact is checked against the page |
| **Offerings catalog** | Matching problems to what we sell | Provided later |

**Note:** the problem statement speaks of "public information". This PRD deliberately goes further: Apollo and Treg are paid data sources and are used next to public web sources.

### 8.1 What the agent searches on the web

This section says exactly what the researchers look for on the web, where, with which searches, and what they take from each source.

#### 8.1.1 General rules for all web research

**Pin down the company first.** Before any angle starts, one step settles who the company is, so researchers don't mix it up with others:
- legal name and short names (e.g. "Deutsche Bahn AG", "DB", "Deutsche Bahn")
- main web domains (e.g. `deutschebahn.com`, `db.de`)
- group structure: parent company, main subsidiaries, brands; which unit the meeting is with
- headquarters country and main countries of operation
- stock ticker, if listed
- names from before a recent rename or takeover

Every search uses these names, and a finding only counts if it is clearly about this company or the unit in the meeting.

**Languages.** Searches run in **English and German** for every angle, and also in the main language of the headquarters country if that is another language (e.g. French for a French group).

**Source ranking.** When sources disagree or a claim needs backing, higher ranks win:

| Rank | Source type | Examples |
|---|---|---|
| 1 | The company's own official documents | Annual report / Geschäftsbericht, investor presentations, ad-hoc and press releases, company website, careers pages, official filings |
| 2 | Official registers and public bodies | Unternehmensregister / Bundesanzeiger, Handelsregister, stock exchange filings (e.g. SEC 10-K for US-listed), public tender portals |
| 3 | The person's own public words | Talks, interviews, podcasts, articles, LinkedIn posts by the attendee |
| 4 | Vendors and partners naming the company | Vendor customer stories and case studies, partner and system-integrator references, joint press releases |
| 5 | Established news and trade press | Reuters, Handelsblatt, FAZ, Financial Times, heise online, Computerwoche, CIO.de, Golem, IT-Daily, The Register, ZDNet |
| 6 | Job postings | Company careers site and its job system, StepStone, Indeed, LinkedIn Jobs, Xing Jobs |
| 7 | Everything else | Blogs, forums, review sites (Glassdoor, kununu), aggregators |

Sources of rank 7 can only produce **signals**, never confirmed facts.

**Skip:** pages written only for search ranking, auto-generated company profile sites with no clear source, content clearly about a different company with a similar name, and pages that can't be read.

**Freshness.** Each finding has a date. Rules per topic:

| Topic | How recent it must be |
|---|---|
| Company basics (business, size, segments) | The latest annual report or newer |
| Leaders and org structure | Checked against sources from the last 6 months |
| Recent changes | The last 12 months (the last 6 months are flagged as "new") |
| Job postings | Open now or posted in the last 6 months; closed postings are noted as closed |
| IT setup and tools | Any age, but each claim carries its date; older than 3 years is marked "may be outdated" |
| People | Current role checked against a source from the last 3 months |

**Depth.** Each angle starts with its standard searches (below), reads the best results, and follows links on those pages to primary sources (e.g. from a news article to the press release, from a press release to the annual report). It stops when its budget is used or when new searches only bring findings it already has.

**Repeat runs.** Searches are limited to content newer than the last run, plus a re-check of facts that are about to become outdated.

#### 8.1.2 Company and business

**Looks for:** what the company does, how it makes money, size, segments and units, countries, strategy, financial situation and pressure, ownership.

| Where | What it takes from there |
|---|---|
| Company website: "About us", "Company", "Strategy", segment pages | Business description, segments, brands, countries, stated strategy |
| Annual report / Geschäftsbericht (latest), half-year and quarter reports | Revenue, profit, employees, segment numbers, strategy programmes, **risk section** (IT, cyber, regulation), named investment areas (digitalisation, cloud, IT modernisation) |
| Investor relations: presentations, capital markets day, earnings call transcripts | Targets, cost programmes, transformation programmes, what management stresses |
| Unternehmensregister / Bundesanzeiger / Handelsregister | Legal entities, financial statements for non-listed companies, managing directors, ownership |
| Sustainability / ESG report | Data centre and energy targets, IT sustainability programmes |
| Press room | Strategy news, new units, partnerships |
| News and business press | Outside view of performance, problems, rumours (marked as signals) |

**Example searches:**
- `"<company>" Geschäftsbericht 2025 filetype:pdf` / `"<company>" annual report 2025`
- `"<company>" Strategie 2030` / `"<company>" strategy transformation programme`
- `"<company>" Umsatz Mitarbeiter` / `"<company>" revenue employees`
- `"<company>" Sparprogramm OR Kostensenkung` / `"<company>" cost cutting programme`
- `site:<company-domain> investor presentation`

#### 8.1.3 IT setup and tools

**Looks for:** the tools and platforms they run, who runs their IT, how IT is organised, and how mature it is. Key areas:
- monitoring, observability and APM
- logging and SIEM
- IT service management (ITSM) and ITOM
- cloud providers and how far they have moved to the cloud
- ERP (e.g. SAP, and whether they are moving to S/4HANA)
- containers and Kubernetes
- networks, mainframe, databases
- IT outsourcing partners and system integrators
- the IT org: CIO/CTO, central vs. local IT, size of the IT team, shared service centres

| Where | What it takes from there |
|---|---|
| **Job postings** (the strongest source here): careers site, its job system (Workday, SuccessFactors, Personio, Greenhouse, etc.), StepStone, Indeed, LinkedIn Jobs, Xing Jobs | Tools named in requirements and in "our stack" sections; teams being built (e.g. "new observability team"); projects named ("S/4HANA move", "cloud migration"); seniority and number of IT hires; locations of IT teams |
| Vendor customer stories and case studies (e.g. Dynatrace, Splunk, Datadog, New Relic, Elastic, ServiceNow, BMC, Microsoft, AWS, Google Cloud, SAP, Red Hat, VMware/Broadcom, IBM) | Tools in use, scope, date, named project owners |
| Partner and system integrator references (e.g. Accenture, Capgemini, Atos, T-Systems, IBM, Bechtle, Cancom, Computacenter, Allgeier) | Who runs or built which part of their IT; outsourcing deals |
| Conference talks and agendas (vendor events such as Dynatrace Perform, ServiceNow Knowledge, AWS re:Invent, KubeCon; German events such as DSAG, DOAG, it-sa) and their recordings on YouTube | Tools, architecture, problems they told the public about, names of speakers |
| Company tech or engineering blog, GitHub organisation | Stack, open-source use, engineering culture |
| Public tenders (TED for the EU, service.bund.de, DTVP, national portals); contract award notices | What IT they are buying, contract values and running times, incumbent suppliers; especially important for public-sector and utilities customers |
| IT trade press (heise, Computerwoche, CIO.de, IT-Zoom, Golem, The Register) | Interviews with their IT leaders, project reports |
| Annual report risk and IT sections | IT investment, cyber risk, outsourcing |
| Company website itself | Visible technology on the site (e.g. cloud provider, monitoring scripts) as a weak signal |

**How findings are marked:**
- A job posting that says "we use X" or "our stack: X" → **fact** that X is in use (dated).
- A job posting that only asks for "experience with X" or "X is a plus" → **signal**.
- A vendor case study naming the company → **fact** for the date of the study; marked "may be outdated" after 3 years.
- A talk by their own staff about their setup → **fact**.

**Example searches:**
- `"<company>" Stellenangebot Dynatrace OR Splunk OR Datadog OR "New Relic" OR Prometheus OR Grafana`
- `"<company>" job observability OR monitoring OR APM OR SRE`
- `"<company>" ServiceNow OR "BMC Helix" OR "Jira Service Management"`
- `"<company>" "S/4HANA" Migration`
- `"<company>" AWS OR Azure OR "Google Cloud" migration`
- `"<company>" case study` / `"<company>" Referenz` / `"<company>" customer story`
- `"<company>" outsourcing IT contract` / `"<company>" IT-Dienstleister`
- `"<company>" ausschreibung IT` / `site:ted.europa.eu "<company>"`
- `"<company>" Kubernetes OR OpenShift talk`
- `site:<careers-domain> IT` (and walk the IT job list)

#### 8.1.4 Recent changes

**Looks for:** anything in the last 12 months that changes their needs or who decides:
- takeovers, mergers, sales of units, carve-outs (these often need new IT or separating IT)
- new CEO, CFO, CIO, CTO, CISO or IT heads; people leaving
- restructuring, cost cuts, layoffs, new strategy programmes
- big IT programmes started or finished (cloud move, ERP change, new data centre, outsourcing or insourcing)
- new rules they must follow (NIS2, DORA for finance, KRITIS for critical infrastructure, the EU AI Act, industry rules)
- new partnerships with IT vendors
- results warnings, big wins or losses

| Where | What it takes from there |
|---|---|
| Company press room and ad-hoc releases | Official changes with dates |
| Business and IT news (Reuters, Handelsblatt, FAZ, heise, Computerwoche, CIO.de, local press for the headquarters region) | Changes, outside views, early signs |
| Leadership pages on the company site (compared to the record) | Leaders who joined or left |
| Vendor and partner press releases | New IT deals |
| Register filings | Changes of managing directors, new entities |
| Job postings (compared to the last run) | Sudden hiring waves or hiring stops in IT |

**Example searches:**
- `"<company>" neuer CIO OR CTO OR CISO` / `"<company>" appoints CIO`
- `"<company>" Übernahme OR Fusion OR Verkauf` / `"<company>" acquisition OR merger OR divestment`
- `"<company>" Restrukturierung OR Stellenabbau` / `"<company>" restructuring layoffs`
- `"<company>" NIS2 OR DORA OR KRITIS`
- `"<company>" Partnerschaft Cloud` / `"<company>" partnership IT`
- all of the above limited to the last 12 months

#### 8.1.5 Signs of trouble

**Looks for:** evidence of pain that IT services could help with:
- outages and service disruptions (apps, online shop, banking app, trains, portals)
- security incidents, ransomware, data leaks
- failed or late IT projects
- IT hiring rushes or hard-to-fill roles (posted again and again)
- complaints from customers or staff about systems
- fines or findings by regulators about IT
- cost pressure on IT

| Where | What it takes from there |
|---|---|
| News and IT press | Outages, incidents, failed projects |
| Public status pages of the company's services | Outage history |
| Outage tracking sites (e.g. allestörungen.de, Downdetector) | Frequency of reported outages (signal only) |
| Security news (heise Security, BleepingComputer, BSI reports) and ransomware leak trackers reported by press | Incidents and dates |
| Job postings over time | Roles open for months or posted repeatedly |
| Employee review sites (kununu, Glassdoor) | Complaints about IT systems or tools (signal only) |
| App store reviews for their main apps | Repeated complaints about app performance (signal only) |
| Regulator press releases (BaFin, data protection authorities, EU bodies) | Fines or findings about IT |

**Example searches:**
- `"<company>" Störung OR Ausfall` / `"<company>" outage`
- `"<company>" Cyberangriff OR Hackerangriff OR Datenleck` / `"<company>" cyberattack OR ransomware OR data breach`
- `"<company>" IT-Projekt gescheitert OR verzögert` / `"<company>" IT project delayed`
- `"<company>" BaFin IT` / `"<company>" fine data protection`

#### 8.1.6 Each attendee

**Looks for:**
- current title, team and how long they have held the role
- career: past employers and roles (what tools and partners they used there can matter)
- education and certificates, when relevant to how technical they are
- what they say in public: talks, interviews, podcasts, articles, posts; topics they care about
- their stated priorities and projects
- shared ground with us (past customers or partners of amasol or klaricore, events, shared contacts known to the rep)
- how to approach them: technical or business, and what they are measured on

| Where | What it takes from there |
|---|---|
| **LinkedIn** (through Apollo, Treg, or text the rep pastes): profile, posts, articles, activity | Title, history, education, topics they post about, recent activity |
| **Xing** (important in DACH) | Title and history, especially for German-speaking people with little LinkedIn activity |
| Company website: leadership and team pages, press releases quoting them | Confirmed title, quotes on priorities |
| Conference speaker pages, agendas, talk recordings | Topics, projects, opinions |
| Interviews and articles in IT and business press | Priorities, projects, opinions |
| Podcasts and webinars | Same |
| Award lists (e.g. "CIO of the Year"), boards and associations | Standing, networks |
| Published papers or books, GitHub profile (for technical people) | Technical depth |

**Rules:**
- The person is matched by name **and** company **and** title. If more than one person could match, the agent asks the rep instead of guessing.
- The brief for each person says what they likely care about and why ("posts often about developer productivity" → probably cares about tooling that speeds up teams), marked as likely.

**Example searches:**
- `"<first name> <last name>" "<company>"`
- `"<first name> <last name>" CIO OR CTO OR "Head of IT"` (with the title the rep gave)
- `"<first name> <last name>" Vortrag OR Interview OR Podcast` / `"<first name> <last name>" talk OR interview OR podcast`
- `site:linkedin.com/in "<first name> <last name>" "<company>"`
- `site:xing.com "<first name> <last name>"`
- `site:<company-domain> "<last name>"`

#### 8.1.7 Extra angles from the meeting goal

When the meeting goal or the record points to a topic, the lead agent adds an angle with its own searches. Examples:

| Meeting goal or topic | Extra searches |
|---|---|
| "Talk about their SAP move" | S/4HANA programme status, SAP partners, SAP-related job postings, DSAG talks |
| "Renewal of monitoring contract" | Current monitoring tools, contract dates from tenders, complaints, competitors' case studies with them |
| "Intro to a new CIO" | The CIO's last employers and what they changed there, interviews since starting, first announcements |
| "Security / compliance" | NIS2/DORA/KRITIS status, incidents, security job postings, CISO talks |
| "Cloud" | Cloud provider deals, cloud job postings, cloud talks, data centre news |

#### 8.1.8 Watching important customers (alerts)

The scheduled check for important customers runs a smaller set of searches limited to content since the last check:
- company press room and news: leaders, takeovers, restructuring, results warnings
- incidents: outages and security incidents
- the leadership page and attendee profiles: who joined or left
- job postings: sudden rise or drop in IT hiring, new tools named
- tenders: new IT tenders from the company

Only changes that pass the "big change" bar (open question Q8) become alerts.

---

## 9. How the agent works (design)

```
Rep in the customer chat
        │
        ▼
┌───────────────────────┐   tools: ask the rep, read/write the record,
│      Lead agent       │          start research, write the brief
│ (talks with the rep)  │
└──────────┬────────────┘
           │ plan: standard angles + one per attendee + extras
           ▼  run at the same time, each with its own context and budget
 ┌─────────┬─────────┬─────────┬─────────┬─────────┐
 │Company  │IT setup │Recent   │Trouble  │Person × │  tools: web search, read page,
 │         │         │changes  │signs    │ N       │         Apollo, Treg (allowed list)
 └────┬────┴────┬────┴────┬────┴────┬────┴────┬────┘
      └─────────┴────┬────┴─────────┴─────────┘
                     ▼  structured findings
              Gap check ──► round 2 (targeted, at most 2 rounds)
                     ▼
              Source check (code)
                     ▼
              Make sense (findings + record + goal + catalog)
                     ▼
              Brief ──► chat + saved;  new facts ──► record
```

- The lead agent keeps the chat clean. Researchers do the heavy reading in their own context and send back only findings.
- Code enforces the guardrails: budgets, the round limit, the source check, and no research without the rep saying go.
- Model choice (proposed): a strong model for the lead agent, the gap check and the make-sense step; a faster, cheaper model for the researchers. To be decided (Q11).

---

## 10. Non-functional requirements

| Area | Requirement (proposed) |
|---|---|
| Speed | First brief in **under 5 minutes**; repeat-meeting brief in **under 3 minutes**; progress shown the whole time |
| Cost | Target cost per brief to be set (Q12); spend is tracked per run and per customer |
| Accuracy | No confirmed statement without a checked source; under 5% of confirmed statements wrong in evaluation |
| Languages | To be decided (Q5): English and/or German briefs; German and English sources |
| Access | Reps log in; who sees which customers to be decided (Q2, Q3) |
| Reliability | If a data source is down, the run continues with the rest and the brief says what was missing |
| Audit | Each brief keeps the findings, sources and data calls it was built from |

---

## 11. Success metrics

- **Time to prepare:** median time from "go" to brief; how much of the rep's prep time is saved (from a survey).
- **Use:** share of customer meetings prepared with the agent; weekly active reps.
- **Quality:** rep rating per brief (useful / partly / not useful); accuracy of confirmed facts in spot checks.
- **Learning loop:** share of meetings where post-meeting notes are added; number of facts per customer over time.
- **Outcome:** share of meetings that reach the suggested next step (rep reports it after the meeting).

---

## 12. Evaluation plan (before launch)

- Pick **10 real customers** that reps already know well.
- For each one, run the agent and have the rep score the brief: correct facts, wrong facts, missed important facts, useful questions, fitting next step.
- Use the same set again after every major change to prompts, angles or sources.
- Compare: one round vs two rounds, with and without Treg, to check what each adds.

---

## 13. Releases

### v1
- Customers, a chat per customer, intake with only the questions it needs
- Lead agent + parallel researchers (web, Apollo, Treg), gap check, source check
- Brief with confirmed/likely/unknown marks, problems, questions, next step
- Customer record and people; learning from post-meeting notes
- Repeat runs that look only for what is new

### v2
- Offerings catalog matching (as soon as the catalog is provided; may move into v1)
- Alerts for important customers
- Brief export
- CRM sync

### Later
- Team view of customers; manager dashboards
- Calendar link: meetings picked up automatically, brief ready the day before

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Made-up or wrong facts | Source check in code; confirmed needs a checked source; rep sees sources |
| Generic briefs | Offerings catalog; angles focused on IT and what we sell; evaluation set |
| Wrong company or wrong person (same name) | Confirm the company at creation; match people by company + title; rep confirms when unsure |
| Cost goes up (Treg per-call, many researchers) | Budgets per researcher and per run; allowed lists of tools; spend tracking |
| Slow runs | Researchers run in parallel; repeat runs look only for what is new; progress shown |
| Stale record | Facts carry dates; old facts marked outdated; repeat runs check them again |

---

## 15. Open questions

| # | Question |
|---|---|
| Q1 | Product name |
| Q2 | amasol and klaricore: one shared workspace, or separate? Can a customer be shared? |
| Q3 | Who sees which customers: every rep, the owner only, or a team? |
| Q4 | Login: company single sign-on (Microsoft) or something simpler for v1? |
| Q5 | Language of briefs: English, German, or the rep's choice? |
| Q6 | Brief export format: PDF, Word, email, or chat only? |
| Q7 | CRM: which one, and should the record sync with it (v1 or later)? |
| Q8 | Alerts: how often to check important customers, and what counts as "big"? |
| Q9 | Alerts: where should the rep be told: email, Teams, in the app? |
| Q10 | Where does it run: web app, inside Teams, or both? Hosting? |
| Q11 | Which AI models for the lead agent and for the researchers? |
| Q12 | Target cost per brief and monthly budget |
| Q13 | Apollo and Treg: which plans and keys, shared or per team? |
| Q14 | Offerings catalog: format and owner (will be provided later) |
| Q15 | How long are records and briefs kept? |
| Q16 | Alerts are part of the core loop in the problem statement but planned for v2 here. Move them into v1? |
