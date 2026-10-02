# Statement of Work — Order Data Migration

**Client:** Brightwater Outfitters
**Consultant:** Riley Okafor (Okafor Systems, independent)
**Effective date:** March 4, 2026
**Client sponsor:** Maya Lindqvist, Director of Operations

## 1. Purpose

Migrate Brightwater Outfitters' historical and live order data out of OrderDesk (the
in-house order system, version 4) into a PostgreSQL 16 warehouse in Brightwater's existing
cloud account, model it with dbt, and stand up daily revenue reporting in Metabase.

## 2. Scope

In scope:

1. Discovery: data audit of OrderDesk, source-to-target data mapping document.
2. Migration build: extraction jobs, PostgreSQL schema, dbt models for orders, customers
   and line items, reconciliation checks against OrderDesk totals.
3. Cutover and hypercare: production cutover, two weeks of hypercare support.
4. One Metabase dashboard: daily revenue by channel.

Out of scope:

- Rebuilding or migrating the returns portal, and any returns or refunds data.
- Changes to the e-commerce storefront.
- Ongoing data engineering after hypercare ends.

## 3. Milestones and fees

This is a **fixed-fee engagement: $48,000** in total, invoiced by milestone.

| Milestone | Deliverable | Target date | Fee |
|---|---|---|---|
| M1 Discovery | Data mapping document | March 20, 2026 | $12,000 |
| M2 Migration build | Reconciled warehouse in staging | April 7, 2026 | $24,000 |
| M3 Cutover and hypercare | Production cutover on April 14, 2026, plus two weeks of hypercare | April 28, 2026 | $12,000 |

Invoices are payable net 15.

## 4. Dependencies

- VPN access and a read-only OrderDesk database login for the consultant by March 10, 2026
  (owner: Tomás Reyes, IT lead).
- A staging and a production database in Brightwater's cloud account.

Dates in section 3 assume these dependencies are met on time.

## 5. Change control

Any work outside section 2 requires a written change request. A change request states the
work, the fee and the schedule impact, and takes effect only once **Maya Lindqvist approves it
in writing**. Email counts as writing.

Signed for Brightwater Outfitters: Maya Lindqvist — March 4, 2026
Signed: Riley Okafor — March 4, 2026
