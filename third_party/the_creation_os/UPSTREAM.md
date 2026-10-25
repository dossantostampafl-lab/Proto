# The Creation OS provenance

Source: https://github.com/dossantostampafl-lab/the-creation-os
Commit: `595b867e942fadab5668edaea0bf44af227fffb8`.
Author/owner attribution: dossantostampafl-lab and original Git history.

The `.py.txt` files are byte-identical originals from the paths listed in
`original-blobs.json`; tests verify their Git blob hashes. They are reference
copies, not imported. Adaptations in `services/chatgpt` retain this provenance.
The integration was requested by the user for the same owner's PROTO project.
No LICENSE/NOTICE file was present in the inspected upstream tree. No open-source
license or broader redistribution permission is inferred or added here. Resolve
licensing before distributing these sources beyond the requested project.

Adaptations: PROTO-specific profile directory/marker; RS256-only ID-token
verification; atomic private writes; credential-file permission checks;
refresh/sign-out/login locking; fixed public inference endpoint; no capability
intent tools; bounded complete-only responses. Provider fallback/bootstrap and
DEUS orchestration were inspected but not copied into the PROTO runtime.

`provider_common.py.txt` is retained only to preserve the original provider's
source dependency; its execution/tool schema is deliberately not imported.
Original contracts and three original offline test files were adapted, with
additional PROTO integration and isolation tests. Original test fixture strings
are not real credentials.
