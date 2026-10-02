Brightwater Outfitters — Order Data Migration kickoff
Date: March 6, 2026, 10:00 Pacific (2026-03-06 18:00 UTC)
Participants: Riley Okafor, Maya Lindqvist, Tomás Reyes, Priya Nandakumar

[00:00] Maya Lindqvist: Thanks everyone. Riley, this is Tomás, our IT lead, and Priya from finance. Let's walk the plan.
[00:22] Riley Okafor: Happy to. Three milestones, as in the SOW: discovery with the data mapping document, the migration build into PostgreSQL, then cutover and two weeks of hypercare. The fee is fixed at forty-eight thousand.
[01:05] Tomás Reyes: On infrastructure — I'd like the warehouse in our existing cloud account, not a new one. We already have backups and monitoring there.
[01:18] Riley Okafor: Agreed. Decision then: PostgreSQL 16 in Brightwater's existing cloud account, staging and production databases.
[01:40] Maya Lindqvist: And the cutover date?
[01:44] Riley Okafor: Target cutover is April 14, provided I have VPN access and a read-only OrderDesk login by March 10.
[02:02] Tomás Reyes: I'll own that. VPN plus the read-only login by March 10.
[02:15] Priya Nandakumar: For reporting, the thing we need is daily revenue by channel. Web, wholesale, and the two retail stores. We pull it by hand from OrderDesk every morning right now.
[02:40] Riley Okafor: That's the Metabase dashboard in the SOW. I'll use your current spreadsheet as the spec — can you send it?
[02:51] Priya Nandakumar: I'll send it today.
[03:05] Riley Okafor: My next deliverable is the data mapping document, due March 13 as a draft, final with M1 on March 20.
[03:30] Maya Lindqvist: Good. Weekly check-ins on Fridays, same time.
[03:41] Riley Okafor: Works for me. Action items: Tomás, VPN and DB login by March 10. Priya, the revenue spreadsheet today. Me, mapping draft by March 13.
