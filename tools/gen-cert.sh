#!/bin/sh
# Issue the companion's server certificate from the CA created by gen-ca.sh.
#
#   gen-cert.sh [--out DIR] IP [IP ...]
#
# The CA is read from the same directory the certificate is written to; there is
# no separate --ca-dir, because splitting them buys nothing and doubles the ways
# to get the invocation wrong.
#
# Exit status: 0 success, 1 refusal (no CA in the output directory), 2 usage
# error (unknown flag, no IP given, an argument that is not an IPv4 address).

set -eu

usage() {
    echo "usage: gen-cert.sh [--out DIR] IP [IP ...]" >&2
}

# A canonical dotted quad and nothing else. A hostname, an IPv6 address and
# 1.2.3.4/24 all fail here, which is the point. A hostname would end up in the
# certificate as a DNS: name, iOS would refuse to match it against the address
# the app actually connects to, and the failure surfaces as a network error
# rather than a certificate one.
is_ipv4() {
    _ip="$1"
    # Reject anything outside digits and dots up front, so the field split below
    # cannot be fooled by whitespace or by a trailing suffix. A trailing dot is
    # caught here rather than in the loop: consuming the separator would leave
    # nothing behind, and "1.2.3.4." would count four octets and pass.
    case "$_ip" in
        "" | *[!0-9.]* | .* | *. | *..* ) return 1 ;;
    esac
    _rest="$_ip"
    _count=0
    _zeros=0
    while [ "$_count" -lt 4 ]; do
        _octet="${_rest%%.*}"
        case "$_rest" in
            *.*) _rest="${_rest#*.}" ;;
            *) _rest="" ;;
        esac
        # An empty field means a leading, trailing or doubled dot.
        [ -n "$_octet" ] || return 1
        [ "${#_octet}" -le 3 ] || return 1
        # A leading zero is ambiguous, not merely ugly: 010.1.1.1 is ten to
        # inet_pton and eight to anything that reads the octet as octal. A
        # certificate is the wrong place to discover which one the reader chose.
        case "$_octet" in
            0?*) return 1 ;;
            0) _zeros=$((_zeros + 1)) ;;
        esac
        [ "$_octet" -le 255 ] || return 1
        _count=$((_count + 1))
    done
    # Anything left over is a fifth field.
    [ -z "$_rest" ] || return 1
    # 0.0.0.0 is the wildcard the plugin refuses to bind (D5), and it is never
    # an address the phone can reach the Pi on.
    [ "$_zeros" -ne 4 ]
}

out_dir="."
ips=""
ip_count=0

while [ $# -gt 0 ]; do
    case "$1" in
        --out)
            if [ $# -lt 2 ]; then
                echo "gen-cert.sh: --out requires a directory" >&2
                usage
                exit 2
            fi
            out_dir="$2"
            shift 2
            ;;
        -*)
            echo "gen-cert.sh: unknown option: $1" >&2
            usage
            exit 2
            ;;
        *)
            if ! is_ipv4 "$1"; then
                echo "gen-cert.sh: not an IPv4 address: $1" >&2
                exit 2
            fi
            ips="$ips $1"
            ip_count=$((ip_count + 1))
            shift
            ;;
    esac
done

if [ "$ip_count" -eq 0 ]; then
    echo "gen-cert.sh: at least one IPv4 address is required" >&2
    usage
    exit 2
fi

ca_key="$out_dir/ca.key"
ca_crt="$out_dir/ca.crt"
srv_key="$out_dir/server.key"
srv_crt="$out_dir/server.crt"

if [ ! -f "$ca_key" ] || [ ! -f "$ca_crt" ]; then
    echo "gen-cert.sh: no CA in $out_dir; run gen-ca.sh --out $out_dir first" >&2
    exit 1
fi

# Everything is built in a temp directory and moved into place only once openssl
# has succeeded, so a failure halfway through cannot leave a key without its
# certificate in the output directory. The CA serial file stays here too, rather
# than appearing as a fifth file next to the four the user is told about.
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT INT TERM

# shellcheck disable=SC2086
set -- $ips
cn="$1"

{
    echo "[req]"
    echo "distinguished_name = dn"
    echo "prompt = no"
    echo
    echo "[dn]"
    echo "CN = $cn"
    echo
    echo "[v3_server]"
    echo "basicConstraints = CA:FALSE"
    echo "keyUsage = critical,digitalSignature,keyEncipherment"
    echo "extendedKeyUsage = serverAuth"
    echo "subjectAltName = @alt_names"
    echo
    echo "[alt_names]"
    # Every address is an iPAddress general name. No DNS: entry is emitted, not
    # even for the CN: the app connects to a bare address over the tether, and
    # a DNS name in the SAN would never be looked at.
    _n=0
    for _ip in "$@"; do
        _n=$((_n + 1))
        echo "IP.$_n = $_ip"
    done
} > "$work_dir/server.cnf"

if ! {
    # 2048-bit RSA with SHA-256, the floor iOS accepts for a server key.
    openssl req -new \
        -newkey rsa:2048 -nodes \
        -keyout "$work_dir/server.key" \
        -out "$work_dir/server.csr" \
        -config "$work_dir/server.cnf" &&
    # 820 days keeps the certificate under the 825-day ceiling iOS enforces,
    # with enough slack that a clock skew between the workstation and the phone
    # cannot push it over.
    openssl x509 -req \
        -in "$work_dir/server.csr" \
        -CA "$ca_crt" \
        -CAkey "$ca_key" \
        -CAcreateserial -CAserial "$work_dir/ca.srl" \
        -out "$work_dir/server.crt" \
        -days 820 \
        -sha256 \
        -extfile "$work_dir/server.cnf" \
        -extensions v3_server
} >"$work_dir/openssl.log" 2>&1; then
    cat "$work_dir/openssl.log" >&2
    echo "gen-cert.sh: openssl could not issue the server certificate" >&2
    exit 1
fi

chmod 0600 "$work_dir/server.key"
chmod 0644 "$work_dir/server.crt"

# Reissuing after an address change is the routine case, so it does not need a
# flag. Requiring one here would train the user to pass --force by reflex, and
# then to pass it to gen-ca.sh as well, where it destroys the trust chain.
mv -f "$work_dir/server.key" "$srv_key"
mv -f "$work_dir/server.crt" "$srv_crt"

echo "tls_cert: $srv_crt"
echo "tls_key:  $srv_key"
echo "Install on the phone: $ca_crt"
