#!/bin/sh
# Create the private root CA that signs the companion's server certificate.
#
#   gen-ca.sh [--out DIR] [--force]
#
# Non-interactive by design: openssl never gets to prompt, so the same invocation
# works from a terminal, from the test suite and from CI.
#
# Exit status: 0 success, 1 refusal (a CA is already there), 2 usage error.

set -eu

usage() {
    echo "usage: gen-ca.sh [--out DIR] [--force]" >&2
}

out_dir="."
force=0

while [ $# -gt 0 ]; do
    case "$1" in
        --out)
            if [ $# -lt 2 ]; then
                echo "gen-ca.sh: --out requires a directory" >&2
                usage
                exit 2
            fi
            out_dir="$2"
            shift 2
            ;;
        --force)
            force=1
            shift
            ;;
        *)
            echo "gen-ca.sh: unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

ca_key="$out_dir/ca.key"
ca_crt="$out_dir/ca.crt"

# Regenerating a CA in place is silent and irreversible: every certificate ever
# issued from the old key stops chaining, and every phone that trusts the old
# root keeps trusting a root that no longer signs anything. The symptom appears
# days later as an app that will not load, with nothing pointing back here. So
# the refusal is the default and the overwrite has to be asked for.
if [ $force -eq 0 ] && { [ -e "$ca_key" ] || [ -e "$ca_crt" ]; }; then
    echo "gen-ca.sh: a CA already exists in $out_dir; pass --force to replace it" >&2
    exit 1
fi

# Everything is built in a temp directory and moved into place only once openssl
# has succeeded, so a failure halfway through cannot leave a key without its
# certificate in the output directory.
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT INT TERM

cat > "$work_dir/ca.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca

[dn]
CN = OpenPwnagotchi Companion CA

[v3_ca]
# "critical" is not decoration. Without it curl still accepts the root, but
# Python's ssl module and iOS reject it, and the resulting error talks about the
# handshake rather than about the extension that is actually missing.
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
EOF

# 4096-bit RSA with SHA-256: iOS refuses weaker keys and SHA-1 signatures
# outright. An EC key would be fine cryptographically but buys nothing here,
# since the key is generated on a workstation and never on the Pi.
if ! openssl req -x509 \
    -newkey rsa:4096 -nodes \
    -keyout "$work_dir/ca.key" \
    -out "$work_dir/ca.crt" \
    -days 3650 \
    -sha256 \
    -config "$work_dir/ca.cnf" >"$work_dir/openssl.log" 2>&1; then
    cat "$work_dir/openssl.log" >&2
    echo "gen-ca.sh: openssl could not create the CA" >&2
    exit 1
fi

chmod 0600 "$work_dir/ca.key"
chmod 0644 "$work_dir/ca.crt"

mkdir -p "$out_dir"
mv -f "$work_dir/ca.key" "$ca_key"
mv -f "$work_dir/ca.crt" "$ca_crt"

echo "CA key:         $ca_key"
echo "CA certificate: $ca_crt"
echo "Install $ca_crt on the phone and enable full trust for it."
