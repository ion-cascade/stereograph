# 0.1 connection verification

Verified on Linux x86-64 with Blender 5.3.0 (`02f0899d78e3`), embedded Python 3.13.13, host Python 3.12.3 and MCP Python SDK 1.30.0.

- **Live Blender window.** Blender built and installed the extension ZIP in a temporary profile. A stdio MCP client identified the fixture and edited a scene property on Blender's main thread. Malformed and oversized messages, an incorrect token, a protocol mismatch, stale session IDs, invalid code and non-serializable results returned structured errors. After a running script timed out, the next request succeeded. Reopening the fixture rotated the session ID and restored the original property. Closing the test window returned the expected unavailable response.
- **Background Blender.** The same MCP client opened the fixture in a fresh process, edited its scene property in memory and captured output. Missing files, execution errors and deadlines returned structured errors. A subsequent request succeeded and saw the original property. The background process received no live connection token.
- **Fixture and isolation.** The disposable file's SHA-256 was unchanged after both paths. The live add-on used a private config, its own token and an ephemeral loopback port. No production Blender file or profile was used.
- **Server distribution.** The built wheel installed into a fresh virtual environment and passed the same background Blender checks through its installed stdio command.
- **Coverage.** The native checks covered the build above. Other operating systems and Blender builds remain unqualified.
