# Test fixtures

Every file here is either synthetic or a reduced sample of an upstream response: at most 10
entries in any list, no user data, no request or response headers, no cookies or tokens. Free
text, people's names, addresses and contact details are replaced with short synthetic
placeholders. Numbers may have been changed, and some fields were dropped or shortened.
Envelopes, field names, types and error codes follow the real responses.

The upstream data belongs to Xueqiu (雪球), Danjuan and the original rights holders. It is not
covered by the MIT License of this repository.

- `replay/<endpoint>/*.json`: responses in the layout of the replay transport
  (`xueqiu_api._replay.ReplayTransport`). A file holds `body`, optional `params` to match on and
  optional `status`, and nothing else. The directory name is a registered endpoint name.
- `waf_challenge.html`: a synthetic page with the markers of the Aliyun WAF challenge page.
