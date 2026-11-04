# Test fixtures

Every file here is either synthetic or a reduced sample of an upstream response: at most 10
entries in any list, no free text, no user data, no request or response headers, no cookies or
tokens. Numbers may have been changed. Envelopes, field names, types and error codes follow the
real responses.

The upstream data belongs to Xueqiu (雪球), Danjuan and the original rights holders. It is not
covered by the MIT License of this repository.

- `replay/<endpoint>/*.json`: responses in the layout of the replay transport
  (`xueqiu_api._replay.ReplayTransport`): `params` to match on, optional `status`, and `body`.
  The quotec and kline bodies are reduced samples from 2026-11-04.
- `waf_challenge.html`: a synthetic page with the markers of the Aliyun WAF challenge page.
