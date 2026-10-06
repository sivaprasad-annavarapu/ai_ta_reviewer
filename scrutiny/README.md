# Scrutiny material

Two kinds of thing live here, and they are kept apart deliberately.

    scrutiny/reference/          material about scrutiny in general
    scrutiny/<CLIENT-CODE>/      one client's own scrutiny record

`reference/` is the source we write canonical rules from: CBDT instructions and
selection criteria, standard questionnaires under section 142(1), the heads a
notice under section 143(2) commonly opens on, ICAI guidance, and any internal
note on what a particular clause tends to attract. Anything in here is about
method, not about a client — it can be read by anyone working on the rule set.

`<CLIENT-CODE>/` is that client's own record: the notices received, the
questionnaires, what was submitted in reply, and the order. Use the same client
code as the filings folder, so `filings/ACME-PVT-LTD` and
`scrutiny/ACME-PVT-LTD` are plainly the same assessee.

Nothing here is read by the analysis engine today — the engine reads
`filings/<CLIENT-CODE>/` only. This folder is where the raw material sits while
we turn it into canonical rules, and it is where the client's own history will
be wired in when the analysis learns to say "this head has been opened before".

Any format is fine here: PDF, DOCX, email exports, notes.
