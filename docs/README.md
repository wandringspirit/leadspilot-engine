# Documentation index

| Document | Read it when you want to… |
| --- | --- |
| [PLAYBOOK.md](PLAYBOOK.md) | **set everything up in order** – mailbox, Google Drive, business types, search, campaign – and know what to do every day |
| [../README.md](../README.md) | install and use the tool (panel, CLI, quick configuration) |
| [OUTREACH.md](OUTREACH.md) | know how the email campaigns work: business types, templates, who gets emailed, when, replies, bounces |
| [AUTOPILOT.md](AUTOPILOT.md) | let the tool find businesses and start campaigns by itself, and read the master sheet in Google Drive |
| [SITES.md](SITES.md) | know how each business's preview website is made, who gets one, how long it stays online and how it is uploaded |
| [CLOUD.md](CLOUD.md) | send from a cloud server that's always on while this computer only searches – roles, the hand-off, the server, moving over |
| [DRIVE.md](DRIVE.md) | know where files go in Google Drive, what a lead file needs, how the sign-in works |
| [ARCHITECTURE.md](ARCHITECTURE.md) | understand the components, data flow, job state machine and the reasoning behind the design |
| [SCRAPING.md](SCRAPING.md) | know exactly how Google Maps is scraped, where each field comes from, the record index map, anti-bot notes and how to repair a layout change |
| [ENRICHMENT.md](ENRICHMENT.md) | know how emails / phones are found on websites, how candidates are filtered and scored, and how duplicate rows are created |
| [DATA_MODEL.md](DATA_MODEL.md) | look up an output column, the CSV format, address / phone rules or the job JSON |
| [API.md](API.md) | drive the crawler from another program |
| [DEVELOPMENT.md](DEVELOPMENT.md) | set up a dev environment, find every setting, run tests, extend the code, keep the docs in sync |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | fix a problem |
| [CHANGELOG.md](CHANGELOG.md) | see what changed between versions |

Diagrams are written in [Mermaid](https://mermaid.js.org/) and render on GitHub / GitLab /
VS Code (with the Markdown Preview Mermaid extension). The documentation is checked against
the code by `tests/test_docs.py` – run `python -m pytest tests/test_docs.py` after editing.
