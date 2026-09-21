# Documentation index

| Document | Read it when you want to… |
| --- | --- |
| [../README.md](../README.md) | install and use the tool (panel, CLI, quick configuration) |
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
