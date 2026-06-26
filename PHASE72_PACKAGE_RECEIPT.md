# Phase72 Package Receipt

The final archive receipt is generated only after the controlled source package
has been created, inspected, and extracted into a clean verification directory.

Required evidence before delivery:

- archive integrity passes `unzip -t`;
- SHA-256 is generated from the final archive and validates with `sha256sum -c`;
- the archive excludes real `.env.local`, credentials, runtime workspaces,
  outputs, logs, caches and bytecode;
- extracted source compiles and passes the Phase72 core verification set.

The final receipt below is updated with the measured archive filename, checksum
and verification result before distribution.
