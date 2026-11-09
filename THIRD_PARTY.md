# Third-party code

This repo's own code is under the MIT license (`LICENSE`). It builds on, and
in a few places quotes, these projects:

| Project | License | Where it appears here |
|---|---|---|
| [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | MIT | downloaded at build time by `hermes/Dockerfile`; `hermes/patches/fixtures/*` are excerpts of its source, used as test fixtures for the patches |
| [vectorize-io/hindsight](https://github.com/vectorize-io/hindsight) | MIT | downloaded at build time by `hermes/Dockerfile` and `hindsight/Dockerfile` |
| [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) | Apache-2.0 | downloaded at build time by `telegram-mcp/Dockerfile`; `telegram-mcp/patches/fixtures/*` are excerpts of its source, used as test fixtures for the patches, and remain under Apache-2.0 |

The fixture files keep their upstream license; see each project for the full
text and copyright notices.
