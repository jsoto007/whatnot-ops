# Sales Ops — Proprietary Software License and Data Terms

**Copyright © 2026 SotoDev. All rights reserved.**

Version 1.0 · Effective 2026-11-11

> **Plain-language summary.** This summary is not part of the terms. If it
> differs from them, the terms below control.
>
> **Your data is yours.** Everything your shop puts into, captures with or
> pulls into Sales Ops, and the reports built from it, belongs to you. SotoDev
> uses it only to run the service for you, never sells it, never trains AI on
> it, and gives you a complete copy when you ask, including when you leave.
>
> **The software is SotoDev's.** The code, the database design, the data
> structures, the algorithms, the parsing rules, the AI prompts and everything
> else that makes Sales Ops work belong to SotoDev, including the parts built
> or customized for you, unless a contract signed by you and SotoDev says
> otherwise. You get a licence to use it, not ownership of it, and SotoDev
> stays free to build similar software for others, without your data.
>
> **Seeing this code is not permission to use it.** If this repository is
> public, GitHub's terms let you view it and fork it on GitHub. Nothing else
> (copying it elsewhere, running, hosting, changing or reusing it, or training
> AI on it) is allowed without a licence from SotoDev.

---

## 1. Definitions

In this License:

- **"SotoDev"** means the individual who owns the Software and does business
  under the name SotoDev, and any business entity that individual owns or
  controls that holds rights in the Software, together with their successors
  and permitted assigns.
- **"Software"** means Sales Ops and everything in this repository or
  delivered by SotoDev in connection with it, in any form and any version,
  including: the web application, API, background worker, browser extension
  ("Chat Scraper") and any other client component; source code, object code,
  bundles and builds; the database schema, table, column, index and
  constraint design, data models, data structures, migrations and migration
  history; data contracts and interface definitions (including
  `shared/chat-event-batch.schema.json`) and the APIs and their request and
  response formats; algorithms, heuristics, parsing and classification rules,
  matching, reconciliation, inventory, order, payment-tracking, shipping,
  labelling, pick-list and reporting logic; AI prompts, instructions, output
  schemas, policies and evaluation sets; export formats, report layouts and
  templates; user-interface designs, layouts, workflows, text and look and
  feel; documentation, plans and runbooks; test suites, fixtures and
  synthetic data; build, deployment and configuration files; every update,
  upgrade, modification, customization, derivative work and improvement of
  any of them, by whomever made, including any part made with the help of
  software tools or AI systems; and every copy, fork or mirror of any of
  them. The Software does not include Client Data or the third-party
  components described in Section 11.1.
- **"Client"** means the person or business to whom SotoDev provides access
  to the Software for the operation of its shop.
- **"Agreement"** means the written terms, other than this License, that
  SotoDev and the Client have agreed for the Software, such as a contract, an
  accepted proposal or statement of work, or an invoice the Client has paid,
  with any written amendments. A **"signed Agreement"** is one signed by both
  SotoDev and the Client, including electronically. Where no Agreement covers
  a point, Section 2.5 applies.
- **"Authorized User"** means an employee or individual contractor of the
  Client whom the Client permits to use the Software on the Client's behalf.
- **"Client Data"** means data that the Client or its Authorized Users enter
  into, upload to, capture with, or cause the Software to import, in whatever
  format or data structure the Software holds it, including captured
  live-show chat, products and stock counts, sales, orders, payment records,
  shipments, buyer names, notes and flags, team accounts, settings, the
  activity log of the Client's shop, and data the Software obtains from a
  third-party platform using the Client's credentials or authorization.
  Client Data also includes the results the Software produces about the
  Client's business from that data (figures, reports, exports, slips, lists
  and AI proposals). Client Data does not include the Software, Service
  Operations Data or Feedback (Section 10).
- **"Service Operations Data"** means technical information about how the
  Software runs, such as logs, error reports, performance and request
  metrics, job and queue status, version and configuration information and
  counts of use. Where it incidentally contains Client Data (for example, a
  username in an error message), that content remains Client Data and is
  protected as Client Data.
- **"Subprocessor"** means a third party that SotoDev engages to host, store
  or otherwise process Client Data for the Software.
- **"Confidential Information"** means non-public information one party
  discloses to the other that is marked confidential or that a reasonable
  person would understand to be confidential. SotoDev's Confidential
  Information includes every part of the Software, and every fact about its
  design, operation and know-how, that SotoDev has not published, including
  server configuration, credentials, prompts, rules, plans and any version
  SotoDev keeps private. Client Data is the Client's Confidential
  Information. The terms and pricing of any Agreement are the Confidential
  Information of both parties.

## 2. How this License applies

2.1 **To everyone.** The Software is protected by copyright, trade-secret
and other law whether or not anyone accepts this License. This License grants
rights only to the Client and its Authorized Users, as Section 5 describes.
Anyone else receives only what Section 13 describes, and any other copying,
use, installation, running, hosting, modification or distribution of the
Software is unlicensed.

2.2 **Acceptance by the Client.** This License becomes a binding agreement
between SotoDev and the Client at the earliest of: (a) the Client signing or
clicking to accept this License, or a signed Agreement that refers to it;
(b) the Client accepting it in writing, including by email; or (c) the Client
continuing to use the Software after SotoDev has given the Client a copy of,
or a link to, this License together with written notice that this License
governs the Software. Until then, the Client's use is governed by whatever
terms the parties have otherwise agreed, and SotoDev's rights under
copyright and trade-secret law apply in any event.

2.3 **Authorized Users.** Authorized Users use the Software on the Client's
behalf, under the Client's acceptance of this License. Before giving anyone
access, the Client will make them aware of the restrictions in Section 6 and
the confidentiality duties in Section 9. Section 5.3 makes the Client
responsible for them.

2.4 **Order of precedence.**

(a) If this License and a signed Agreement conflict, the signed Agreement
controls to the extent of the conflict.

(b) This License controls over every other Agreement document. Terms in a
purchase order, vendor portal or other form of the Client's do not apply,
even if SotoDev accepts the form or performs after receiving it.

(c) If a signed Agreement made before this License bound the Client assigns
or licenses rights in any part of the Software to the Client, that Agreement
governs those rights to the extent it does so, and this License governs
everything else.

(d) In any Agreement made after this License binds the Client, general words
such as "deliverables", "work product" or "work made for hire" do not assign
or transfer the Software or any part of it; only an express assignment that
meets Section 3.3 does.

(e) No Agreement reduces the Client's ownership of Client Data, or the
protections in Sections 4, 7 and 8, unless it says so expressly and the
Client signs it.

2.5 **Where there is no Agreement.** To the extent no Agreement covers the
point: (a) the licence runs month to month until either party ends it on
thirty (30) days' written notice; (b) the fees are those SotoDev quotes or
invoices in writing and the Client accepts or pays; (c) the shops covered
are those SotoDev has set up in the Software for the Client; and (d) notices
go to the addresses described in Section 21.6.

## 3. SotoDev owns the Software

3.1 **Ownership.** SotoDev owns and retains all right, title and interest,
including all copyrights, trade secrets, patent rights, trademark rights,
database rights and other intellectual-property rights anywhere in the world,
in and to the Software and every copy of it. This includes the design,
selection, coordination and arrangement of the Software's schema, data models
and data structures, independent of the Client Data stored in them.

3.2 **What is protected.** SotoDev's rights cover all protectable expression
in the Software, including its source and object code; its structure,
sequence and organization; and the selection, coordination, arrangement,
modification and integration of its parts, including parts that SotoDev
created with the help of software tools or AI systems under its direction,
selection and review. The Client's obligations under this License are
contractual and apply to every part of the Software, whether or not that
part, on its own, is protected by copyright or is a trade secret.

3.3 **No transfer by payment or customization.** Paying for, requesting,
specifying, funding or testing any part of the Software, including features
built only for the Client, buys the licence in Section 5 and does not
transfer ownership. No part of the Software is a "work made for hire" for the
Client. Ownership of the Software or any part of it passes only by a written
assignment, signed by SotoDev, that expressly identifies what is assigned.

3.4 **Improvements.** Every modification, enhancement or derivative work of
the Software (but not Client Data), whoever makes it and whether or not it was
suggested by, requested by or made with the Client, belongs to SotoDev when
created. The Client hereby assigns to SotoDev, and will have its Authorized
Users assign, any right it or they acquire in one; waives, and will have them
waive, any moral rights in it to the extent law allows; and will sign the
documents reasonably needed to confirm this. To the extent an assignment is
not effective, the Client grants SotoDev a non-exclusive, perpetual,
irrevocable, worldwide, royalty-free, transferable licence, with the right to
sublicense, to use and exploit that right in any way, and agrees not to
assert it against SotoDev or SotoDev's licensees.

3.5 **No exclusivity.** Unless a signed Agreement expressly grants
exclusivity, nothing in this License or any Agreement limits SotoDev from
developing, using, licensing or providing the Software, or anything similar
to it, including features first built for the Client, to anyone, including
other sellers on the same platforms, so long as SotoDev does not use or
disclose Client Data or the Client's Confidential Information to do so.

3.6 **Reservation of rights.** All rights not expressly granted in Section 5,
or required by the GitHub terms described in Section 13, are reserved by
SotoDev. No licence is granted by implication, estoppel, exhaustion, course
of dealing or otherwise.

## 4. The Client owns its data

4.1 **Ownership.** As between SotoDev and the Client, the Client owns all
right, title and interest in and to Client Data. SotoDev claims no ownership
of Client Data, and nothing in this License or any Agreement transfers
ownership of Client Data to SotoDev.

4.2 **The data and what holds it are different things.** Client Data remains
the Client's even though it is stored in, organized by and produced through
the Software's schema, data models and data structures. The Client's
ownership of Client Data gives it no right in the Software's schema, data
models, data structures, algorithms, report layouts or formats, except the
right in Section 8.4 to use exports to read, keep and move its own data.

4.3 **Licence to SotoDev.** The Client grants SotoDev a non-exclusive,
worldwide, royalty-free licence, for as long as SotoDev holds Client Data as
this License permits, to host, copy, store, transmit, process and display
Client Data **only** to: (a) provide, maintain, support, secure, back up and
correct the Software for the Client; (b) do what the Client or its Authorized
Users ask; (c) prevent or investigate fraud, abuse or security incidents;
(d) comply with law; and (e) establish, exercise or defend legal claims about
the Software or this License, using only what is reasonably needed. SotoDev
may let its Subprocessors exercise this licence only for those purposes. This
licence ends as Section 8 describes.

## 5. Licence granted to the Client

5.1 **Grant.** Subject to this License and the Agreement, SotoDev grants the
Client a limited, non-exclusive, non-transferable (except under Section 21.3),
non-sublicensable licence, during the term in Section 15.1, for its
Authorized Users to: (a) access and use the hosted Software that SotoDev
operates for the Client; (b) download the browser extension from the
Software, install it, in the unmodified form SotoDev provides, on devices
that the Client owns or controls or that an Authorized User uses for the
Client's work, and use it there; and (c) use the documentation SotoDev
provides for those purposes; in each case solely for the Client's own
internal operation of the shop or shops covered by the Agreement (or by
Section 2.5).

5.2 **Conditions and scope.** The licence is expressly conditioned on
(a) the Client's compliance with Section 6, and (b) payment of undisputed
fees, so that none remains unpaid both more than thirty (30) days after its
due date and ten (10) days after SotoDev's written notice that it is overdue.
The restrictions in Section 6 limit the scope of the licence. Use outside
that scope, or while a condition is unmet, is unlicensed as well as a breach
of this License. SotoDev may suspend or end the licence only as Sections 14.3
and 15 provide.

5.3 **Authorized Users and devices.** The Client is responsible for its
Authorized Users; for keeping their credentials and paired devices secure;
for promptly removing access for anyone who should no longer have it; and for
everything done with the Software under its accounts or devices, as if done
by the Client. The Client will tell SotoDev promptly if it learns of any
unauthorized access to its accounts or of any use of the Software that
breaches this License.

5.4 **Reviewing the extension.** The browser extension runs on the Client's
devices, and its code can be read there. The Client's personnel and
professional advisers who are bound by confidentiality duties at least as
protective as Section 9 may examine the extension's code solely to assess,
for the Client, its security and what data it reads and sends. What they
learn is SotoDev's Confidential Information and may not be used for any
purpose that Section 6 prohibits.

5.5 **Everyone else.** If you are not the Client or an Authorized User, this
License grants you no right to use, copy, install, run, host, modify or
distribute the Software or any part of it, except as Section 13 describes.

## 6. Restrictions

Except as this License or a signed Agreement expressly permits, the Client
will not, and will not permit or knowingly help anyone else to:

1. copy, modify, translate, adapt or create derivative works of the Software,
   except for the copies Section 5.1 allows and copies made automatically in
   normal use (such as a browser's cache);
2. decompile, disassemble, reverse engineer, de-obfuscate or otherwise
   attempt to derive source code, algorithms, rules, prompts, schemas, data
   models or data structures from the Software, except for the review
   Section 5.4 allows and to the limited extent applicable law expressly
   permits despite this restriction;
3. sell, resell, rent, lease, lend, sublicense, distribute, publish, post,
   host, offer as a service, time-share or otherwise make the Software or any
   part of it (including the extension package, its code or the
   documentation) available to anyone other than its Authorized Users;
4. self-host, deploy or run the Software, or any server-side part of it,
   outside the environment SotoDev operates or approves in writing;
5. use the Software or SotoDev's Confidential Information to design, build,
   train, benchmark or improve a product or service that competes with the
   Software, or to copy or recreate any feature, workflow, user interface,
   schema, data model or algorithm of the Software (this does not stop the
   Client from using another product, or from moving Client Data to one as
   Section 8.4 allows);
6. use the Software or any part of it, including its source code, schema,
   prompts, rules or documentation, to train, fine-tune, evaluate or ground
   any machine-learning or AI model or system, submit any of them to such a
   model or system, or include any of them in a dataset (this does not limit
   what the Client does with its own Client Data);
7. scrape, crawl, bulk-extract or systematically download the Software's
   code, interface or documentation, or access the Software by automated
   means other than the interfaces SotoDev provides;
8. remove, alter or obscure any copyright, licence, trademark or
   confidentiality notice, or any copyright management information, in or on
   the Software;
9. bypass, disable, defeat or interfere with any licence, pairing,
   device-key, authentication, rate-limiting, permission, activity-logging or
   other security or technical protection measure in the Software; use a
   device key or session outside the Software; or let more than one person
   use the same login;
10. use the Software in violation of law, to infringe or misappropriate
    anyone's rights, or to store or send unlawful or malicious content; or
11. perform penetration, vulnerability or load testing of the hosted Software
    without SotoDev's prior written consent, or disclose to anyone other than
    SotoDev the results of any security or performance testing or
    benchmarking of the Software. The Client is encouraged to report suspected
    vulnerabilities to SotoDev privately.

## 7. How SotoDev handles Client Data

7.1 **Use only for the Client.** SotoDev processes Client Data only for the
purposes in Section 4.3. SotoDev does not sell, rent or trade Client Data,
does not use or disclose it for advertising, does not use it to provide
products or services to anyone else, and keeps it logically separated from
other customers' data.

7.2 **No AI training on Client Data.**

(a) SotoDev will not use Client Data to train, fine-tune or otherwise
develop any machine-learning or AI model.

(b) When an AI feature is on, SotoDev sends to the AI provider only the
Client Data that feature needs, and only to a provider listed in Section 7.4
or approved by the Client in writing.

(c) SotoDev will send Client Data to an AI provider only under terms that,
when the data is sent, do not allow the provider to train its models on it.
If those terms change to allow that, SotoDev will stop sending Client Data to
that provider unless the Client approves in writing. Those terms may let the
provider keep data for a limited time for its own safety, abuse-monitoring
or legal purposes, which this Section does not prevent.

(d) The same rules apply to AI tools that SotoDev uses itself to build and
support the Software: SotoDev will not give them Client Data unless they are
used under terms or settings that do not allow training on it.

(e) The Client may ask SotoDev to turn AI features off at any time.

7.3 **Confidentiality and access.** SotoDev keeps Client Data confidential
under Section 9. Within SotoDev, only people who need Client Data for a
purpose in Section 4.3, and who are bound by confidentiality obligations at
least as protective as this License, may access it. The Software keeps an
owner-level support account for SotoDev ("IT Support") in the Client's shop.
Because SotoDev needs it to operate and support the Software, the Client
cannot remove, demote or disable it. SotoDev uses it only to provide support,
maintenance and security, or when the Client asks, and actions taken through
it in the Software are recorded in the Software's activity log. As operator
of the hosting, SotoDev also has administrative access to the servers and
database, which it uses only for the purposes in Section 4.3; that access is
not recorded in the Software's activity log. SotoDev treats any platform
credential or token the Client provides as the Client's Confidential
Information, uses it only to run the Software for the Client, and stops using
and deletes it when the licence ends. The Client may revoke such a
credential or token at the platform at any time.

7.4 **Subprocessors.** As of this version, SotoDev's Subprocessors for the
Software are: Render (hosting, managed database, backups and logs);
Cloudflare (network delivery and security in front of the hosting), while it
is used for that; Mailgun
(sending account, invitation and password-reset email); and Anthropic (AI
classification), only while an AI feature that uses it is on. SotoDev's own
business email and file-storage providers may also hold Client Data that the
Client sends to SotoDev or that Section 7.6 allows; SotoDev will name them on
request. Separately, the web app loads typefaces from Google Fonts, which
receives the network address and browser details of each person who opens
it but no Client Data; and the Software connects to Whatnot only on the
Client's authorization, as the Client's own platform and not as SotoDev's
Subprocessor. SotoDev will give the Client written notice at least fourteen
(14) days before a new Subprocessor begins processing Client Data, unless a
shorter period is needed to keep the Software running or secure, in which
case SotoDev will give notice promptly. If the Client reasonably objects on
data-protection grounds and SotoDev cannot reasonably accommodate the
objection, the Client may end the licence on written notice and receive a
refund of prepaid fees for the period after the end, as its sole remedy for
the objection. SotoDev is responsible for its Subprocessors' processing of
Client Data as for its own, subject to Section 17.

7.5 **Security.** SotoDev will maintain reasonable administrative, technical
and physical safeguards designed to protect Client Data against unauthorized
access, use, disclosure, loss and alteration, appropriate to the nature of
the data and the size of SotoDev's business, and at least those applicable
law requires of SotoDev. No system is perfectly secure, and SotoDev does not
guarantee that Client Data will never be accessed without authorization. The
Client is responsible for the security of its own devices, browsers, accounts
and credentials, including those for third-party platforms.

7.6 **Support copies and test data.** Where SotoDev needs a copy of Client
Data outside the production system (for example, to diagnose a problem or to
import a spreadsheet the Client provides), SotoDev keeps it only as long as
needed for that purpose, protects it under this License, deletes it when it
is no longer needed (and in any case as Section 8.2 requires), and never
commits it to any source-code repository. SotoDev may learn from the patterns
it sees in Client Data to correct and improve how the Software handles data
of that kind, but will not copy Client Data into the Software's code, tests,
fixtures, prompts or documentation. Any examples SotoDev writes for them are
synthetic and do not reproduce Client Data or identify the Client or any
person.

7.7 **Security incidents.** If SotoDev confirms unauthorized access to, or
acquisition, disclosure, loss or alteration of, Client Data in systems
operated by SotoDev or its Subprocessors (a **"Security Incident"**), SotoDev
will: (a) notify the Client without undue delay, and in any case within any
time limit applicable law sets for SotoDev; (b) give the Client the
information SotoDev reasonably has, and update it as SotoDev learns more;
(c) take reasonable steps to contain and remedy it; and (d) reasonably
cooperate with the notices the Client decides, or is required, to give. As
owner of the data, the Client decides whether and how to notify affected
people and authorities, unless the law requires SotoDev to do so.
Unsuccessful attempts, such as scans or blocked log-in attempts, are not
Security Incidents. A notice under this Section is not an admission of fault.

7.8 **Personal data of others.** Client Data may include information about
the Client's buyers, viewers and staff (such as usernames, chat messages,
email addresses and the Client's notes and flags about buyers). The Client
decides what personal data the Software collects and why; is responsible for
having the rights, notices and consents needed to collect it and to have
SotoDev process it; and will not ask SotoDev to process it in a way that
violates law or a platform's terms. The Client will not put into the Software
payment-card numbers, bank-account numbers, government identification
numbers, health information or other sensitive personal data, other than what
viewers themselves type into chat that the Software captures in ordinary use.
SotoDev processes personal data in Client Data only on the Client's behalf
and under this Section 7.

7.9 **U.S. state privacy laws.** To the extent the California Consumer
Privacy Act or another U.S. state consumer-privacy law applies to personal
information in Client Data, SotoDev acts as the Client's "service provider"
or "processor" for it and will: (a) process it only for the business purposes
in Section 4.3, following the Client's instructions in this License and the
Agreement; (b) not sell or share it (as those laws define those words), and
not retain, use or disclose it for any other purpose or outside its direct
business relationship with the Client; (c) not combine it with personal
information from other sources, except as those laws permit; (d) comply with
the obligations those laws place on SotoDev, give it the level of protection
they require, and tell the Client if SotoDev determines it can no longer meet
them; (e) let the Client take reasonable and appropriate steps, including
through the information in Section 19.3, to confirm SotoDev's compliance and
to stop and remedy any unauthorized use; (f) reasonably help the Client
respond to requests from individuals exercising their rights under those
laws, through the Software's features or, beyond them, at SotoDev's
then-current rates; and (g) bind each Subprocessor to written terms with
equivalent protections. On the Client's request, SotoDev will sign a
data-processing addendum that adds any further terms such a law requires for
the Client's use of the Software.

7.10 **Legal demands.** If SotoDev is legally required to disclose Client
Data, it will, where lawful, give the Client prompt notice so the Client can
seek protection; will disclose only what is legally required; and will
reasonably cooperate with the Client's lawful efforts to limit the
disclosure.

7.11 **Service Operations Data and residual knowledge.** SotoDev may collect
and use Service Operations Data to operate, secure, support and improve the
Software, and may keep it after the licence ends. SotoDev may also freely use
the general skills, knowledge, experience, ideas, concepts and techniques it
gains or improves while providing the Software, including its understanding
of the kinds of data and situations the Software must handle, so long as it
does not disclose Client Data or the Client's Confidential Information or
identify the Client. Neither right lets SotoDev keep or use Client Data
itself, or any dataset derived from it, for any other purpose.

## 8. Export, return and deletion of Client Data

8.1 **Export at any time.** While the licence is in effect, the Client may
export Client Data using the Software's export features. At any time,
including during a suspension, the Client may also ask SotoDev for a complete
export, which SotoDev will provide within thirty (30) days. The first such
export in any twelve (12) month period is free; further ones are charged at
SotoDev's then-current rates. The Client should keep its own exports of
anything it cannot afford to lose.

8.2 **When the licence ends.** On the Client's written request made within
thirty (30) days after the licence ends, SotoDev will provide one complete
export of Client Data in a commonly used machine-readable format (such as
CSV, JSON or a database dump), at no additional charge and whether or not
any fees are outstanding. Passwords, device keys and other security
credentials are not exported. SotoDev will then delete Client Data from the
production system, and the copies Section 7.6 describes, within thirty (30)
days after that export window closes (or after delivering the export, if
later). Copies in backups and in Subprocessors' logs are deleted as they
expire in the ordinary course; until then they remain protected under this
License and are not restored except to recover from a failure, in which case
restored Client Data is deleted again. SotoDev may keep Client Data for
longer only where law requires or where it is needed for a pending or
reasonably anticipated legal claim, and protects it under this License until
it is deleted. The Client may ask SotoDev to delete Client Data sooner.
SotoDev will confirm deletion in writing on request.

8.3 **If SotoDev stops providing the Software.** SotoDev will give the Client
at least thirty (30) days' written notice before it permanently stops
providing the hosted Software to the Client for any reason other than the
Client's breach, unless events described in Section 21.10 prevent that
notice. Section 8.2 then applies as if the licence had ended on the date in
the notice.

8.4 **Exports do not license the Software.** An export may reveal the
structure needed to read it (such as field names and layouts). The Client may
use exports to read, keep and move its own data, including into another
system, but an export grants no licence to the Software's schema, data
models, data structures, algorithms or formats for any other purpose,
including any purpose that Section 6 prohibits.

## 9. Confidentiality and trade secrets

9.1 **Obligations.** Each party will use the other's Confidential
Information only to exercise its rights and perform its obligations under
this License and the Agreement; will not disclose it except to its personnel
and professional advisers who need to know it and are bound by duties of
confidentiality at least as protective as this Section; and will protect it
with at least reasonable care.

9.2 **Exceptions.** Confidential Information does not include information
that is or becomes public through no fault of the receiving party, was
rightfully known to the receiving party without restriction before
disclosure, is rightfully received from a third party without restriction,
or is independently developed without use of the other party's Confidential
Information. These exceptions do not apply to Client Data as compiled in the
Software, even where individual items (such as chat messages shown during a
live show) were visible to the public. Information that SotoDev publishes
(Section 13) is public only from when, and only to the extent that, it is
published; that does not make public any part of the Software, or any
information about it, that has not been published, including later versions
SotoDev keeps private.

9.3 **Compelled disclosure.** A party may disclose the other's Confidential
Information to the extent law or a court requires, after giving the other
party prompt notice where lawful and reasonable help to seek protection.
Section 7.10 also applies to Client Data.

9.4 **Duration.** These obligations last for the term of the licence and five
(5) years after it ends and, for trade secrets and Client Data, for as long
as the information remains a trade secret or Client Data.

9.5 **Trade secrets.** The Software's algorithms, rules, prompts, data
models, methods, configuration and know-how, to the extent not generally
known, are SotoDev's trade secrets. SotoDev takes reasonable measures to keep
them secret, including limiting access to them and requiring confidentiality
under this License. Nothing in this License limits the protection available
under trade-secret law, including the U.S. Defend Trade Secrets Act and New
York law.

9.6 **Protected disclosures.** Nothing in this License prevents anyone from
reporting a possible violation of law to a government authority, from making
a disclosure protected by whistleblower law, or from disclosing a trade
secret as permitted by 18 U.S.C. § 1833(b).

## 10. Feedback

If the Client or its Authorized Users give SotoDev ideas, suggestions,
feature requests, bug reports or other feedback about the Software
("Feedback"), SotoDev may use, incorporate and commercialize that Feedback
without restriction or compensation, and any resulting change to the
Software belongs to SotoDev. SotoDev will not publicly identify the Client as
the source of Feedback without the Client's consent. Feedback does not
include Client Data, which stays the Client's.

## 11. Third-party components, platforms and trademarks

11.1 **Open-source components.** The Software includes third-party
open-source components, each licensed under its own terms (see `NOTICE.md`).
Those terms govern those components, and nothing in this License limits a
right that an open-source licence grants for its component. Those components
are not "the Software" for purposes of SotoDev's ownership.

11.2 **Third-party platforms.** The Software works with platforms and
services that SotoDev does not control, including Whatnot, Google Chrome,
email and AI providers. The Client decides whether to use the Software with
each platform and is solely responsible for that decision; for complying
with the terms, policies and API terms of every platform it connects to or
uses the Software with, including Whatnot's seller and API terms; for
obtaining and safeguarding its own platform accounts, credentials and tokens;
and for the charges it incurs on those platforms (including shipping labels
purchased through them). SotoDev makes no representation that using the
Software with any platform complies with that platform's terms. SotoDev is
not responsible for changes, outages, restrictions or account actions by
those platforms, or for features that stop working because a platform
changes.

11.3 **No affiliation.** Whatnot is a trademark of its owner. Sales Ops and
SotoDev are not affiliated with, endorsed by or sponsored by Whatnot or any
other platform named in the Software or its documentation. Platform names are
used only to describe compatibility.

11.4 **Names and marks.** "SotoDev", "Sales Ops", "Chat Scraper" and their
logos are SotoDev's trademarks or trade names. This License grants no right
to use them except to refer to the Software accurately. SotoDev may describe
the Software and the kind of work it did in its portfolio, without naming the
Client or revealing Client Data; naming the Client publicly as a customer
requires the Client's consent, which may be given by email.

## 12. AI-assisted and automated features

The Software uses rules and, where enabled, AI models to propose and, in
limited cases, apply classifications such as sales, products and review
decisions; changes applied this way are recorded in the activity log. These
outputs are probabilistic and may be wrong. The Client is responsible for
reviewing outputs before relying on them, and for the decisions it makes
about its buyers, orders, payments, stock, shipping and money. The Client may
ask SotoDev to turn AI features off (Section 7.2). Figures shown by the
Software are operational aids; the platform's own records (for example,
Whatnot's records of sales, payments and payouts) are the authoritative
source.

## 13. Public visibility of this repository

13.1 **Visibility is not permission.** SotoDev may make this repository, or
parts of the Software, publicly visible on GitHub or elsewhere. Doing so is
not a licence (except as Section 13.2 describes), an open-source release, a
dedication to the public or a waiver of any right.

13.2 **What GitHub's terms allow.** GitHub's Terms of Service, as in effect
from time to time, provide that the owner of a public repository grants each
GitHub user a non-exclusive licence to use, display and perform its contents
through GitHub's service and to reproduce them solely on GitHub as GitHub's
functions permit (for example, by forking the repository within GitHub).
SotoDev grants that licence only to the extent those terms require, and
nothing more. Except to the extent those terms require otherwise, no one may,
because of that licence or because the Software is visible: copy the
Software outside GitHub's service (other than temporary copies needed to view
it); install, run, deploy or host it; use it in or for any business; modify
it; or distribute it.

13.3 **Forks and copies.** The Software in every copy, fork or mirror,
wherever it is, remains SotoDev's and subject to this License, and each copy,
fork or mirror must keep this License and all notices intact.

13.4 **No AI training or data mining.** SotoDev does not permit anyone to
copy, collect or use the Software to train, fine-tune or evaluate any
machine-learning or AI model, or to include it in any dataset, except GitHub
to the extent its terms require. Any such use needs a separate written
licence from SotoDev.

## 14. Fees, payment and suspension

14.1 **Fees.** Fees are set by the Agreement (or Section 2.5(b)). Fees
exclude taxes; the Client will pay all sales, use, value-added and similar
taxes on them, other than taxes on SotoDev's net income. Fees paid are
non-refundable except as this License or the Agreement expressly provides.

14.2 **Payment.** Unless the Agreement says otherwise, invoices are due
thirty (30) days after the invoice date. The Client must raise any good-faith
dispute about an invoice in writing, with its reasons, within fifteen (15)
days after receiving it, and must pay the undisputed part on time; the
parties will work in good faith to resolve the dispute. Overdue undisputed
amounts bear interest at one percent (1%) per month, or the highest rate the
law allows if that is lower, from the due date until paid.

14.3 **Suspension.** SotoDev may suspend the Client's access to all or part
of the Software, after written notice where practicable: (a) if undisputed
fees are more than thirty (30) days overdue and remain unpaid ten (10) days
after SotoDev's written notice; (b) if needed to prevent or stop a security
threat or material harm to the Software, Client Data or anyone else; (c) if
continued access would violate law; or (d) if the Client materially breaches
Section 6 or 9. SotoDev will limit a suspension to what is reasonably needed
and restore access promptly once the cause is resolved. Suspension does not
delete Client Data or end the Client's rights under Sections 7 and 8, and
SotoDev will provide an export under Section 8.1 during it on request.

## 15. Term and termination

15.1 **Term.** The licence starts when SotoDev first gives the Client access
to the Software and lasts for the term of the Agreement, or, under Section
2.5(a), until either party ends it.

15.2 **Termination.** Either party may end the licence as the Agreement or
Section 2.5 provides. Either party may also end it on written notice if the
other materially breaches this License and does not cure the breach within
thirty (30) days after written notice describing it (ten (10) days for
unpaid undisputed fees). SotoDev may end it immediately on written notice if
the Client materially breaches Section 3, 6 or 9 in a way that cannot
reasonably be cured. If the Client ends the licence because of SotoDev's
uncured material breach, SotoDev will refund prepaid fees for the period
after the end.

15.3 **Effect.** When the licence ends: all rights granted to the Client end;
the Client and its Authorized Users must stop using the Software, remove the
browser extension from every device and delete every copy of the extension
package; the Client must return or destroy SotoDev's Confidential
Information and confirm this in writing on request; fees that accrued before
the end remain payable; and Section 8 applies to Client Data.

15.4 **Survival.** Sections 1, 2.4, 3, 4, 6, 7 (for as long as SotoDev holds
Client Data), 8 through 13, 14.1 and 14.2 (for amounts that accrued before
the end), 15.3, 15.4 and 16 through 22 survive the end of the licence.

## 16. Warranty disclaimer

EXCEPT FOR SOTODEV'S EXPRESS COMMITMENTS IN SECTIONS 7 AND 8, EXCEPT AS
EXPRESSLY STATED IN A SIGNED AGREEMENT, AND TO THE MAXIMUM EXTENT PERMITTED BY
LAW, THE SOFTWARE IS PROVIDED "AS IS" AND "AS AVAILABLE", WITH ALL FAULTS.
SOTODEV DISCLAIMS ALL WARRANTIES, EXPRESS, IMPLIED OR STATUTORY, INCLUDING ANY
WARRANTY OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE,
NON-INFRINGEMENT, ACCURACY, QUIET ENJOYMENT, AND ANY WARRANTY ARISING FROM
COURSE OF DEALING OR USAGE OF TRADE. SOTODEV DOES NOT WARRANT THAT THE
SOFTWARE WILL BE UNINTERRUPTED, ERROR-FREE OR SECURE OR MEET ANY AVAILABILITY
LEVEL; THAT IT WILL CAPTURE EVERY CHAT MESSAGE OR CLASSIFY EVERY SALE
CORRECTLY; THAT AI OUTPUTS WILL BE ACCURATE; THAT USING IT COMPLIES WITH ANY
PLATFORM'S TERMS; OR THAT IT WILL KEEP WORKING WITH ANY THIRD-PARTY PLATFORM.

## 17. Limitation of liability

17.1 **Excluded damages.** EXCEPT AS SECTION 17.4 PROVIDES, AND TO THE
MAXIMUM EXTENT PERMITTED BY LAW, NEITHER PARTY WILL BE LIABLE TO THE OTHER
FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY OR PUNITIVE
DAMAGES; FOR LOST PROFITS, REVENUE, SALES, GOODWILL OR BUSINESS
OPPORTUNITIES, WHETHER DIRECT OR INDIRECT; FOR LOSS OR CORRUPTION OF DATA,
EXCEPT AS SECTION 17.3 PROVIDES; OR FOR THE COST OF SUBSTITUTE GOODS OR
SERVICES; HOWEVER CAUSED AND UNDER ANY THEORY OF LIABILITY, EVEN IF ADVISED
OF THEIR POSSIBILITY.

17.2 **Cap.** EXCEPT AS SECTION 17.4 PROVIDES, AND TO THE MAXIMUM EXTENT
PERMITTED BY LAW, EACH PARTY'S TOTAL LIABILITY FOR ALL CLAIMS ARISING OUT OF
OR RELATING TO THE SOFTWARE, THIS LICENSE AND ANY AGREEMENT WILL NOT EXCEED
THE GREATER OF (A) THE FEES PAID AND PAYABLE BY THE CLIENT TO SOTODEV FOR THE
SOFTWARE IN THE TWELVE (12) MONTHS BEFORE THE FIRST EVENT GIVING RISE TO
LIABILITY, AND (B) ONE THOUSAND U.S. DOLLARS (US$1,000).

17.3 **Data costs are direct damages.** The reasonable costs of
(a) notifications that law requires because of a Security Incident caused by
SotoDev's breach of Section 7, and (b) restoring Client Data from available
backups, or re-creating it from the Client's own records, after it is lost or
corrupted because of SotoDev's breach of this License, are direct damages
that Section 17.1 does not exclude. They remain subject to Section 17.2.

17.4 **Not limited.** Sections 17.1 and 17.2 do not limit: (a) the Client's
liability for breach of Section 3 or 6, for breach of Section 9 as to
SotoDev's Confidential Information, or for infringing or misappropriating
SotoDev's intellectual property; (b) the Client's obligations to pay fees and
under Section 18.1; (c) either party's liability for its gross negligence,
wilful misconduct or fraud; or (d) any liability that cannot be limited or
excluded by law.

17.5 **Basis of the bargain.** The parties agree these limits reflect an
agreed allocation of risk, are an essential basis of the bargain, and apply
even if a limited remedy fails of its essential purpose.

## 18. Indemnification and infringement claims

18.1 **By the Client.** The Client will defend SotoDev and its personnel
against any claim, demand, suit or proceeding brought by a third party
(including a platform, a buyer, a viewer, a member of the Client's staff or a
government authority), and will pay the losses, damages, fines, penalties,
settlements and reasonable costs and attorneys' fees resulting from it, to
the extent it arises out of: (a) Client Data, or its collection, storage or
use as the Client or its Authorized Users directed, including a claim that it
violates anyone's privacy or other rights or any law; (b) the Client's or its
Authorized Users' use of the Software with any third-party platform or
account, including a claim by the platform that this use breaches its terms;
(c) the Client's breach of this License or the Agreement; or (d) the
Client's violation of law. The Client has no obligation under this Section to
the extent the claim results from SotoDev's breach of this License, gross
negligence or wilful misconduct, or is a claim that the Software itself
infringes a third party's intellectual property, which Section 18.3 covers.

18.2 **Procedure.** SotoDev will give the Client prompt written notice of the
claim (a delay relieves the Client only to the extent it is prejudiced),
allow the Client to control its defence and settlement with counsel
reasonably acceptable to SotoDev, and reasonably cooperate at the Client's
expense. SotoDev may take part with its own counsel at its own cost. The
Client will not settle a claim in a way that imposes any obligation,
admission or restriction on SotoDev without SotoDev's prior written consent.

18.3 **Infringement claims about the Software.** If the Software becomes, or
SotoDev reasonably believes it is likely to become, the subject of a claim
that it infringes or misappropriates a third party's intellectual property,
SotoDev may, at its option and expense: (a) obtain the right for the Client
to keep using it; (b) modify or replace it so that it no longer infringes,
with materially similar functions; or (c) if neither is commercially
reasonable, end the licence for the affected part (or all of it) on written
notice and refund prepaid fees for the period after that. Section 8 then
applies to Client Data. To the extent law allows, this Section states
SotoDev's entire obligation, and the Client's exclusive remedy, for any
claim that the Software infringes or misappropriates anyone's rights.

## 19. Enforcement

19.1 **Equitable relief.** A breach or threatened breach of Section 3, 6 or
9, or of Section 7 as to Client Data, may cause the other party irreparable
harm for which money damages would not be an adequate remedy. That party may
seek an injunction or other equitable relief to prevent or stop it, in
addition to all other remedies and, to the extent the court permits, without
posting a bond or proving actual damages.

19.2 **Unauthorized use.** Any use, copy, modification or distribution of the
Software outside this License is unlicensed and infringes SotoDev's
copyright, and may also misappropriate SotoDev's trade secrets. This License
does not waive any remedy available under copyright, trade-secret or other
law.

19.3 **Compliance information.** On reasonable written request, no more than
once in any twelve (12) month period, the Client will certify in writing
that it is using the Software in compliance with this License, and SotoDev
will give the Client the written information reasonably needed to show its
compliance with Sections 7 and 8.

19.4 **Attorneys' fees.** In any action or proceeding arising out of or
relating to this License, the prevailing party may recover its reasonable
attorneys' fees, costs and expenses, including on appeal and in enforcing a
judgment.

## 20. Governing law and disputes

20.1 **Governing law.** This License, and any dispute arising out of or
relating to it or the Software, whether in contract, tort or otherwise, are
governed by the laws of the State of New York, without regard to
conflict-of-laws rules that would apply another law, and by applicable U.S.
federal law, including the U.S. Copyright Act and the Defend Trade Secrets
Act. The United Nations Convention on Contracts for the International Sale of
Goods and the Uniform Computer Information Transactions Act, in any form
enacted, do not apply.

20.2 **Resolving disputes first.** Before starting a lawsuit, other than to
seek relief under Section 19.1 or to collect undisputed fees, a party will
give the other written notice describing the dispute, and the parties will
try in good faith to resolve it for thirty (30) days.

20.3 **Courts.** Subject to Section 20.2, the state courts located in New
York County, New York, and the United States District Court for the Southern
District of New York have exclusive jurisdiction over any dispute arising out
of or relating to this License or the Software. Each party submits to their
personal jurisdiction and waives any objection to venue there, including on
the ground of inconvenient forum. Either party may, however, seek relief
under Section 19.1 in any court of competent jurisdiction.

20.4 **Jury waiver.** TO THE EXTENT PERMITTED BY LAW, EACH PARTY KNOWINGLY
AND VOLUNTARILY WAIVES ANY RIGHT TO A JURY TRIAL IN ANY DISPUTE ARISING OUT OF
OR RELATING TO THIS LICENSE OR THE SOFTWARE.

20.5 **Time limit for claims.** To the extent permitted by law, a claim by
either party arising out of or relating to this License or the Software must
be started within one (1) year after the party bringing it knew, or
reasonably should have known, of the facts giving rise to it, or it is
permanently barred. This does not apply to claims for unpaid fees, claims
for infringement or misappropriation of intellectual property, or claims for
breach of Section 3, 4, 6, 7, 8 or 9, which keep the period the law
provides. The period is extended by the time the parties spend under Section
20.2.

## 21. General

21.1 **Entire agreement.** This License and the Agreement are the entire
agreement between SotoDev and the Client about the Software and replace all
earlier discussions, representations and understandings about it that are
not part of the Agreement, subject to Section 2.4(c). Each party confirms that
it has not relied on any statement, representation or promise that is not
set out in this License or a signed Agreement.

21.2 **Changes to this License.** SotoDev may publish new versions of this
License. A new version applies to everyone other than the Client from when it
is published. It binds the Client only when the Client accepts it in writing,
or thirty (30) days after SotoDev gives the Client written notice of it if
the Client keeps using the Software after that period without objecting in
writing. If the Client objects within that period, the version that last
bound the Client continues to apply until the end of the current term (or,
under Section 2.5, until either party ends the licence). No new version
reduces the Client's ownership of Client Data, or the protections in
Sections 4, 7 and 8, without the Client's written acceptance.

21.3 **Assignment and subcontracting.** The Client may not assign or transfer
this License, or any right or obligation under it, whether voluntarily, by
merger, by operation of law, by change of control or otherwise, without
SotoDev's prior written consent. SotoDev will not unreasonably withhold
consent to a transfer to a buyer of all or substantially all of the Client's
shop business who agrees in writing to be bound by this License and is not a
competitor of SotoDev. Any other attempt is void. SotoDev may assign or
transfer this License, in whole or in part, without consent, to an affiliate
(including a business entity that SotoDev's owner forms or controls) or to a successor to all or
part of its business or the Software, with notice to the Client; any
successor is bound by the Client's ownership of Client Data and by Sections
7, 8 and 9. SotoDev may use subcontractors to provide the Software, remains
responsible for them, and will bind any who access Client Data to Sections 7
and 9.

21.4 **Severability and reformation.** If any provision is held
unenforceable, it will be enforced to the maximum extent permissible and
reformed to the minimum extent necessary, and the rest of this License
remains in effect.

21.5 **No waiver.** A failure or delay in enforcing any provision is not a
waiver of it. A waiver must be in writing and signed by the waiving party.

21.6 **Notices.** Notices under this License must be in writing and are given
when delivered by hand, by a recognized courier, or by email to the address
in the Agreement or another address the receiving party designates in
writing; where there is none, to the email address that party most recently
used to correspond with the other about the Software. A notice of breach,
termination, a claim or a change to this License must say that it is given
under this License.

21.7 **Electronic signatures and records.** This License, any Agreement, and
any acceptance, notice, consent or assignment under them may be made and
signed electronically. To the extent law allows, an electronic record or
signature satisfies any requirement that something be in writing or signed.

21.8 **Export control.** The Client will comply with all applicable export
control and sanctions laws and will not use or access the Software from, or
make it available to anyone in, a country or to a person subject to U.S.
embargo or sanctions.

21.9 **U.S. Government users.** The Software is "commercial computer
software" and related documentation is "commercial computer software
documentation" under FAR 12.212 and DFARS 227.7202; government users acquire
only the rights in this License.

21.10 **Force majeure.** Neither party is liable for a delay or failure to
perform (other than to pay money) caused by events beyond its reasonable
control, such as natural disasters, war, terrorism, labour actions,
government action, epidemics, power or internet failures, and outages or
changes of third-party platforms, hosting providers or networks. This does
not excuse SotoDev from taking the steps in Section 7.7 after a Security
Incident.

21.11 **Independent parties.** The parties are independent contractors. This
License creates no partnership, joint venture, agency, fiduciary or
employment relationship. There are no third-party beneficiaries, except that
the persons Section 18.1 protects may enforce it.

21.12 **Interpretation.** "Including" means "including without limitation".
"Written" and "in writing" include email, except where this License requires
a signature. "Days" means calendar days. References to Sections are to
sections of this License. Headings and the plain-language summary are for
convenience only and do not affect interpretation. Each party has had the
opportunity to review this License with counsel, and it will not be
construed against either party as its drafter.

## 22. Contact

Licensing, permissions, data requests and notices: SotoDev, at the contact
details in the Client's Agreement or the email address SotoDev uses to
correspond with the Client. Anyone else may contact SotoDev through the
repository owner's profile at <https://github.com/jsoto007>.
