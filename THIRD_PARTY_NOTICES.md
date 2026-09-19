# Third-Party Notices

This source release does not redistribute third-party source trees or binary dependencies. The Windows bootstrapper installs the exact direct and transitive versions in `requirements.lock.txt` into a project-local virtual environment and then runs `pip check`.

| Component | Version | License | Purpose |
|---|---:|---|---|
| annotated-doc | 0.0.4 | MIT | FastAPI documentation metadata |
| annotated-types | 0.7.0 | MIT | Pydantic annotated constraints |
| anyio | 4.14.2 | MIT | Async compatibility layer |
| certifi | 2026.5.20 | MPL-2.0 | Certificate authority bundle for HTTPX |
| click | 8.1.8 | BSD-3-Clause | Uvicorn command support |
| colorama | 0.4.6 | BSD-3-Clause | Windows terminal compatibility |
| exceptiongroup | 1.3.0 | MIT | Python 3.10 compatibility |
| fastapi | 0.141.1 | MIT | Web application and REST API framework |
| greenlet | 3.5.1 | MIT | SQLAlchemy execution support |
| h11 | 0.16.0 | MIT | HTTP/1.1 protocol implementation |
| httpcore | 1.0.9 | BSD-3-Clause | HTTPX transport layer |
| httpx | 0.28.1 | BSD-3-Clause | FastAPI test client transport |
| idna | 3.17 | BSD-3-Clause | Internationalized domain handling |
| iniconfig | 2.3.0 | MIT | pytest configuration |
| itsdangerous | 2.2.0 | BSD-3-Clause | Signed local demo sessions |
| Jinja2 | 3.1.6 | BSD-3-Clause | Server-rendered HTML templates |
| MarkupSafe | 3.0.3 | BSD-3-Clause | Jinja2 escaping |
| packaging | 25.0 | Apache-2.0 OR BSD-2-Clause | Version and requirement handling |
| pluggy | 1.6.0 | MIT | pytest plugin system |
| pydantic | 2.13.4 | MIT | FastAPI data validation |
| pydantic_core | 2.46.4 | MIT | Pydantic validation engine |
| Pygments | 2.20.0 | BSD-2-Clause | pytest terminal output |
| psycopg | 3.3.4 | LGPL-3.0-only | Optional PostgreSQL adapter for the production-shaped persistence path |
| psycopg-binary | 3.3.4 | LGPL-3.0-only | Optimized binary implementation installed by the optional Psycopg binary extra |
| pytest | 9.1.1 | MIT | Automated test runner |
| python-multipart | 0.0.32 | Apache-2.0 | Form and attachment parsing |
| SQLAlchemy | 2.0.52 | MIT | Database mapping and queries |
| starlette | 1.6.0 | BSD-3-Clause | FastAPI web foundation |
| tomli | 2.3.0 | MIT | Python 3.10 TOML support |
| tzdata | 2026.2 | Apache-2.0 | IANA time zone data fallback for business-calendar calculations on Windows |
| typing_extensions | 4.16.0 | PSF-2.0 | Cross-version typing support |
| typing-inspection | 0.4.2 | MIT | Pydantic/FastAPI typing inspection |
| uvicorn | 0.52.4 | BSD-3-Clause | Local ASGI server |

The package names and versions above are also recorded in `SBOM.json`. The authoritative license text and notices for each dependency are supplied by its publisher when the dependency is installed. Third-party components remain governed by their respective licenses.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
