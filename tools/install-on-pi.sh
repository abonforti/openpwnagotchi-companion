#!/bin/sh
# Install the companion on a pwnagotchi: the plugin and the built PWA.
#
#   install-on-pi.sh [--web-root DIR] [--plugins-dir DIR] [--config FILE]
#                    [--tag vX.Y.Z] [--archive FILE] [--repo owner/name]
#                    [--plugin-only] [--web-only] [--dry-run] [--pwn-prefix DIR]
#                    [--require-attestation]
#
# Runs on the unit, as root. Non-interactive by design: the same invocation
# works by hand, from the test suite and from a future CI job.
#
# Both halves are installed, because a web root serving the app with no plugin
# behind it is a working server with nothing to talk to.
#
# It never edits config.toml. Enabling the plugin and setting its options is the
# user's step, documented in docs/SETUP.md: a script that rewrites the main
# configuration of a running pwnagotchi can break far more than it installs.
#
# Exit status: 0 success, 1 failure (missing tool or source file, no release,
# download, checksum, an archive that refuses to stay inside its target, an
# unresolved custom plugins directory, a build provenance refusal with
# --require-attestation - SPEC.md 5.3, issue #182), 2 usage error, including
# a value read from a file on the unit that fails the checks in
# require_clean_dir_value (SPEC.md 5.3.1, issue #166), and
# --require-attestation given together with --plugin-only, since provenance
# is only checked on the web half. Anything non-zero leaves the installation
# as it was.

set -eu

usage() {
    echo "usage: install-on-pi.sh [--web-root DIR] [--plugins-dir DIR] [--config FILE]" >&2
    echo "                        [--tag vX.Y.Z] [--archive FILE] [--repo owner/name]" >&2
    echo "                        [--plugin-only] [--web-only] [--dry-run] [--pwn-prefix DIR]" >&2
    echo "                        [--require-attestation]" >&2
}

# Strip leading and trailing spaces and tabs. Written out rather than shelled
# out to sed: this runs on a Pi Zero where the pwnagotchi process is already the
# heavy thing on the box, and the config file is parsed a line at a time.
trim() {
    _s="$1"
    while :; do
        case "$_s" in
            " "* | "	"*) _s="${_s#?}" ;;
            *" " | *"	") _s="${_s%?}" ;;
            *) break ;;
        esac
    done
    printf '%s' "$_s"
}

# Unwrap a TOML string value. Both quote styles are accepted because both appear
# in configurations people actually have; an unquoted value is taken up to the
# first space, which is as far as this parser is willing to guess.
unquote() {
    _v=$(trim "$1")
    case "$_v" in
        # The closing quote is found rather than assumed to be the last
        # character: `custom_plugins = "/etc/x"  # note` is legal TOML, and
        # taking the whole remainder would yield a path with quotes and a
        # comment inside it, which then gets created as a directory.
        '"'*) _v="${_v#\"}" ; _v="${_v%%\"*}" ;;
        "'"*) _v="${_v#\'}" ; _v="${_v%%\'*}" ;;
        # An unquoted value stops at the first space or comment marker, which is
        # as far as this parser is willing to guess.
        *) _v="${_v%% *}" ; _v="${_v%%#*}" ;;
    esac
    printf '%s' "$_v"
}

# Read main.custom_plugins out of a TOML file, printing nothing when it is not
# there. Two spellings are in circulation and both have to be understood: the
# key inside a [main] table, and the dotted form at the top level, which is what
# the image's own config.toml uses. A key under [main.plugins.something] is not
# the same key and must not match, which is why the current table is tracked
# rather than grepping the file for the word.
read_custom_plugins() {
    _file="$1"
    _table=""
    _found=""
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(trim "$_line")
        case "$_line" in
            "" | "#"*) continue ;;
            "["*"]")
                _table="${_line#[}"
                _table="${_table%]}"
                _table=$(trim "$_table")
                continue
                ;;
        esac
        case "$_line" in
            *=*) ;;
            *) continue ;;
        esac
        _key=$(trim "${_line%%=*}")
        _val="${_line#*=}"
        # Quoted keys are legal TOML and nothing on the image writes them, so
        # they are left alone rather than half-supported.
        if [ "$_table" = "main" ] && [ "$_key" = "custom_plugins" ]; then
            _found=$(unquote "$_val")
        elif [ -z "$_table" ] && [ "$_key" = "main.custom_plugins" ]; then
            _found=$(unquote "$_val")
        fi
    done < "$_file"
    printf '%s' "$_found"
}

# Refuse an archive that would unpack outside the directory it is aimed at. GNU
# tar strips a leading slash and warns, busybox tar does not, and this runs as
# root: the difference between the two is the difference between a web root and
# an overwritten /etc. Checked before extraction, not after, because after is
# too late.
check_archive_paths() {
    _archive="$1"
    if ! tar -tzf "$_archive" > "$work_dir/entries.txt" 2> "$work_dir/tar.log"; then
        cat "$work_dir/tar.log" >&2
        echo "install-on-pi.sh: cannot read $_archive as a gzip archive" >&2
        return 1
    fi
    # Names are not the whole story. A symlink member `x -> /etc` followed by a
    # regular member `x/passwd` has two entirely innocent-looking names, and tar
    # extracting them in order as root writes through the link. Link members are
    # refused outright: a built PWA has no legitimate use for one.
    if ! tar -tvzf "$_archive" > "$work_dir/entries-v.txt" 2> "$work_dir/tar.log"; then
        cat "$work_dir/tar.log" >&2
        echo "install-on-pi.sh: cannot list $_archive" >&2
        return 1
    fi
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            l* | h*)
                echo "install-on-pi.sh: refusing archive, link member: $_line" >&2
                return 1
                ;;
        esac
    done < "$work_dir/entries-v.txt"

    while IFS= read -r _entry || [ -n "$_entry" ]; do
        case "$_entry" in
            "" ) continue ;;
            /* | ".." | "../"* | *"/../"* | *"/..")
                echo "install-on-pi.sh: refusing archive, unsafe path: $_entry" >&2
                return 1
                ;;
        esac
    done < "$work_dir/entries.txt"
    return 0
}

# Verify the archive against a SHA256SUMS file, matching on the file name so a
# sums file covering several assets still works. A mismatch is a hard failure:
# an archive that fails its checksum is either corrupt or hostile and there is
# no third case.
verify_checksum() {
    _archive="$1"
    _sums="$2"
    _name="${_archive##*/}"
    _actual=$(sha256sum "$_archive")
    _actual="${_actual%% *}"
    _expected=""
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(trim "$_line")
        case "$_line" in
            "" | "#"*) continue ;;
        esac
        _hash="${_line%% *}"
        _entry=$(trim "${_line#* }")
        # "sha256sum -b" prefixes the name with an asterisk, and some producers
        # write "./name". Neither changes which file the line is about.
        _entry="${_entry#\*}"
        _entry="${_entry#./}"
        # Matched as written rather than on the basename: a line for
        # `other/dist.tgz` describes a different file, and letting it satisfy a
        # bare `dist.tgz` is how a sums file for one build blesses another.
        if [ "$_entry" = "$_name" ]; then
            if [ -n "$_expected" ]; then
                echo "install-on-pi.sh: $_sums has more than one entry for $_name" >&2
                return 1
            fi
            _expected="$_hash"
        fi
    done < "$_sums"
    if [ -z "$_expected" ]; then
        echo "install-on-pi.sh: $_sums has no entry for $_name" >&2
        return 1
    fi
    if [ "$_expected" != "$_actual" ]; then
        echo "install-on-pi.sh: checksum mismatch for $_name" >&2
        echo "  expected $_expected" >&2
        echo "  actual   $_actual" >&2
        return 1
    fi
    echo "Checksum OK:    $_name"
    return 0
}

# Ask a second question beside the checksum: did this repository build the
# archive, not just does it match the sums it shipped with (SPEC.md 5.2, 5.3,
# issue #182). Three outcomes, told apart because a check that could not run
# is not a clean one (SPEC.md 13):
#
#   ok           a signed attestation for $_archive verifies against $repo,
#                built by $repo/.github/workflows/release.yml specifically
#                (--signer-workflow), so the answer is "release.yml built
#                it" and not "someone in this repository attested something".
#   absent       $_kind is "local" (a hand-built --archive, which nothing
#                attested) or gh reports no attestation for this release (a
#                release published before attestation existed). A warning
#                that names what was checked, and the run continues.
#   unverifiable the question itself could not be asked: gh is missing, gh
#                is installed but not logged in (the verifier needs a token
#                even for a public repository), or the answer from GitHub
#                is not a verdict - an HTTP 401, 403 or 5xx, or a
#                connection that never completed. A warning worded
#                differently from "absent", and the run continues.
#   invalid      an attestation exists and fails verification, or gh says
#                anything else. Always exit 1: a check that fails open on
#                an unrecognised message is not a check, and a file that
#                verifies against nothing this repository built is worse
#                than a file with no sums at all.
#
# gh's own exit codes do not distinguish these (`gh help exit-codes`
# documents 0 success, 1 "fails for any reason", 2 cancelled, 4 requires
# authentication - and 4 was not what `gh attestation verify` actually
# returned for bad credentials when this was checked by hand, only 1, the
# same code an absent attestation or a failed verification also returns).
# What does distinguish them is the text gh prints on stderr, checked by
# hand the same way: 404 is no attestation for this subject (absent); not
# logged in is its own sentence, checked separately below before verify
# ever runs; 401, 403 and 5xx, and the small set of network-failure
# phrases below, are gh failing to reach GitHub at all, not a verdict
# about the artifact (unverifiable). The phrase list is deliberately
# narrow: gh's own verification failures are certificate- and identity-
# centric (a wrong --signer-workflow, an expired cert, an unrecognised
# issuer), so generic words like "certificate" or "timeout" would read a
# real, negative verdict as "could not ask" instead - failing open on the
# exact answer this check exists to catch. Only phrases a verdict cannot
# carry are matched: they name a failure to complete the request, not a
# request that completed and said no.
#
# --require-attestation turns "absent" and "unverifiable" into exit 1 too,
# for an owner who wants the stricter rule; "invalid" is already exit 1
# regardless. It is refused at the top of the script, before any of this
# runs, when paired with --plugin-only: the check only happens in the web
# half, so there would be nothing to attest.
verify_provenance() {
    _archive="$1"
    _kind="$2"

    if [ "$_kind" = "local" ]; then
        echo "install-on-pi.sh: provenance: $_archive is a hand-built archive; nothing attested it, continuing" >&2
        if [ $require_attestation -eq 1 ]; then
            echo "install-on-pi.sh: --require-attestation refuses an archive with no attestation to check" >&2
            return 1
        fi
        return 0
    fi

    if ! command -v gh >/dev/null 2>&1; then
        echo "install-on-pi.sh: provenance: gh is not on PATH, could not ask whether $tag is attested; continuing" >&2
        if [ $require_attestation -eq 1 ]; then
            echo "install-on-pi.sh: --require-attestation refuses an install whose provenance could not be checked" >&2
            return 1
        fi
        return 0
    fi

    # A token is required even to verify an attestation on a public repository,
    # so an installed-but-unauthenticated gh has to be told apart from one
    # that ran and got a real answer: gh auth status is the question asked
    # first, before verify ever runs, rather than trying to recognise its
    # failure text after the fact (SPEC.md 5.3, issue #182).
    if ! gh auth status >/dev/null 2>&1; then
        echo "install-on-pi.sh: provenance: gh is not logged in (gh auth login), could not ask whether $tag is attested; continuing" >&2
        if [ $require_attestation -eq 1 ]; then
            echo "install-on-pi.sh: --require-attestation refuses an install whose provenance could not be checked" >&2
            return 1
        fi
        return 0
    fi

    _err_file="$work_dir/attestation.err"
    if gh attestation verify "$_archive" --repo "$repo" \
        --signer-workflow "$repo/.github/workflows/release.yml" \
        >"$work_dir/attestation.out" 2>"$_err_file"; then
        echo "Attestation OK: $tag ($repo)"
        return 0
    fi
    _stderr=$(cat "$_err_file")
    case "$_stderr" in
        *"HTTP 404"*)
            echo "install-on-pi.sh: provenance: no attestation found for $tag ($repo); continuing" >&2
            if [ $require_attestation -eq 1 ]; then
                echo "install-on-pi.sh: --require-attestation refuses a release with no attestation" >&2
                return 1
            fi
            ;;
        *"HTTP 401"* | *"HTTP 403"* | *"HTTP 5"* | *"dial tcp"* | *"no such host"* | \
        *"i/o timeout"* | *"connection refused"* | *"context deadline exceeded"* | *"x509:"*)
            echo "install-on-pi.sh: provenance: could not ask whether $tag is attested (gh attestation verify could not reach GitHub): $_stderr" >&2
            if [ $require_attestation -eq 1 ]; then
                echo "install-on-pi.sh: --require-attestation refuses an install whose provenance could not be checked" >&2
                return 1
            fi
            ;;
        *)
            echo "$_stderr" >&2
            echo "install-on-pi.sh: provenance verification failed for $tag ($repo)" >&2
            return 1
            ;;
    esac
    return 0
}

# Pull "tag_name" out of a release document without a JSON parser. The field is
# on its own line in what the API returns, and the alternative is depending on
# python3 to read three characters.
read_tag_name() {
    _json="$1"
    _tag=""
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            *'"tag_name"'*)
                _rest="${_line#*\"tag_name\"}"
                _rest="${_rest#*:}"
                _rest=$(trim "$_rest")
                case "$_rest" in
                    '"'*)
                        _rest="${_rest#\"}"
                        _tag="${_rest%%\"*}"
                        ;;
                esac
                break
                ;;
        esac
    done < "$_json"
    printf '%s' "$_tag"
}

# Validate a value on its way to becoming a write destination: absolute, no
# whitespace, no ".." segment. One rule for every route the plugins directory
# or the web root can arrive by - a flag, main.custom_plugins in config.toml,
# the same key in defaults.toml, or --pwn-prefix on the way to that file -
# rather than three checked routes and a fourth an oversight (SPEC.md 5.3.1,
# issue #166). A directory with a space in its name is one no pwnagotchi image
# ships and no owner chooses on purpose; a ".." segment is a path the unit
# itself would honour and this script would write to, and nothing about it
# says "mistake" until the plugin is not where the unit looks for it. None of
# this is a privilege boundary, since whoever passes the flag or owns the file
# is already root; it is the script saying what it will and will not write to.
# The route name identifies where the value came from, so the message says
# which route failed.
#
# Must be called at top level, never inside a command substitution: it exits
# the script on failure rather than returning, and an exit inside $(...) only
# ends the subshell, leaving the caller to go on with an empty or unvalidated
# value.
require_clean_dir_value() {
    _route="$1"
    _value="$2"
    case "$_value" in
        /*) ;;
        *)
            echo "install-on-pi.sh: $_route must be an absolute path: $_value" >&2
            exit 2
            ;;
    esac
    case "$_value" in
        *[[:space:]]*)
            echo "install-on-pi.sh: $_route must not contain whitespace: $_value" >&2
            exit 2
            ;;
    esac
    case "$_value" in
        *"/../"* | *"/..")
            echo "install-on-pi.sh: $_route must not contain a .. segment: $_value" >&2
            exit 2
            ;;
    esac
}

web_root="/var/www/openpwn-companion"
plugins_dir=""
config_file="/etc/pwnagotchi/config.toml"
# Named the image's package tree, so the third resolution step for
# main.custom_plugins is a seam rather than a literal (SPEC.md 5.3.1, issue
# #157): a test can point this elsewhere instead of writing into a real
# /opt/.pwn, and a unit whose tree lives somewhere else is handled the same way.
pwn_prefix="/opt/.pwn"
tag=""
archive=""
repo="abonforti/openpwnagotchi-companion"
# `repo` has a default, so its value cannot tell us whether the user named it.
repo_given=0
plugin_only=0
web_only=0
dry_run=0
require_attestation=0

while [ $# -gt 0 ]; do
    case "$1" in
        --web-root)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --web-root requires a directory" >&2
                usage
                exit 2
            fi
            # An empty or relative value makes the staging directory `.new` in
            # whatever directory the script was started from, and that
            # directory is rm -rf'd on the way past.
            require_clean_dir_value --web-root "$2"
            web_root="$2"
            shift 2
            ;;
        --plugins-dir)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --plugins-dir requires a directory" >&2
                usage
                exit 2
            fi
            require_clean_dir_value --plugins-dir "$2"
            plugins_dir="$2"
            shift 2
            ;;
        --config)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --config requires a file" >&2
                usage
                exit 2
            fi
            config_file="$2"
            shift 2
            ;;
        --pwn-prefix)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --pwn-prefix requires a directory" >&2
                usage
                exit 2
            fi
            # It is interpolated into a glob, not a URL, so --tag's character
            # check does not apply (SPEC.md 5.3.1) - a strange value simply
            # fails to match and the script refuses, which is what it would do
            # anyway. Whitespace matters here beyond the general rule: the
            # value is expanded unquoted so the glob still matches, and
            # $defaults_list accumulates matches space-separated so it can be
            # counted with a plain `for`; whitespace in the prefix would
            # word-split both, so a match would go missed and the count would
            # be wrong rather than merely inconvenient.
            require_clean_dir_value --pwn-prefix "$2"
            pwn_prefix="$2"
            shift 2
            ;;
        --tag)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --tag requires a tag" >&2
                usage
                exit 2
            fi
            # Validated for the same reason as --repo below: it is
            # interpolated into the download URL, and a `..` segment is
            # normalised away by curl, which would fetch a different
            # repository whose SHA256SUMS matches its own archive.
            case "$2" in
                "" | *[!A-Za-z0-9._-]*)
                    echo "install-on-pi.sh: --tag must be a version tag: $2" >&2
                    exit 2
                    ;;
            esac
            tag="$2"
            shift 2
            ;;
        --archive)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --archive requires a file" >&2
                usage
                exit 2
            fi
            archive="$2"
            shift 2
            ;;
        --repo)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --repo requires owner/name" >&2
                usage
                exit 2
            fi
            # Validated because it is interpolated into a URL. The host cannot
            # be moved from here, but a `..` segment is normalised away by curl
            # and would quietly fetch a different repository on github.com,
            # whose SHA256SUMS matches its own archive and so verifies happily.
            case "$2" in
                */*/* | *[!A-Za-z0-9._/-]* | */ | /* | *..*)
                    echo "install-on-pi.sh: --repo must be owner/name: $2" >&2
                    exit 2
                    ;;
                */*) ;;
                *)
                    echo "install-on-pi.sh: --repo must be owner/name: $2" >&2
                    exit 2
                    ;;
            esac
            repo="$2"
            repo_given=1
            shift 2
            ;;
        --plugin-only)
            plugin_only=1
            shift
            ;;
        --web-only)
            web_only=1
            shift
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        --require-attestation)
            require_attestation=1
            shift
            ;;
        *)
            echo "install-on-pi.sh: unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

# Asking for one half and then for the other half is not a request for both, it
# is a typo, and silently doing everything would hide it.
if [ $plugin_only -eq 1 ] && [ $web_only -eq 1 ]; then
    echo "install-on-pi.sh: --plugin-only and --web-only are mutually exclusive" >&2
    usage
    exit 2
fi

# Build provenance is only asked about on the web half - the archive it names
# is the PWA, not the plugin - so --require-attestation beside --plugin-only
# is a request for a check that never runs, not a stricter one.
if [ $require_attestation -eq 1 ] && [ $plugin_only -eq 1 ]; then
    echo "install-on-pi.sh: --require-attestation has nothing to attest with --plugin-only" >&2
    usage
    exit 2
fi

# --archive names a file, --tag and --repo name a release. Given both, one of
# them is going to be ignored, and the user has no way to tell which: the run
# reports success having installed a build they did not ask for.
if [ -n "$archive" ] && { [ -n "$tag" ] || [ $repo_given -eq 1 ]; }; then
    echo "install-on-pi.sh: --archive names a local file, --tag and --repo name a release;" >&2
    echo "  pass one source, not two" >&2
    usage
    exit 2
fi

do_plugin=1
do_web=1
[ $web_only -eq 0 ] || do_plugin=0
[ $plugin_only -eq 0 ] || do_web=0

# Every external tool is checked here, in one message, rather than being
# discovered when the download has already happened and half the work is done.
missing=""
for tool in curl tar sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    echo "install-on-pi.sh: missing required tool(s):$missing" >&2
    exit 1
fi

# The plugin ships next to this script, in the checkout it was run from; the
# release archive carries the PWA only.
script_dir="."
case "$0" in
    */*) script_dir="${0%/*}" ;;
esac
plugin_src="$script_dir/../plugin/companion.py"

work_dir=$(mktemp -d)
staging=""
# The staging directory is removed too: an interrupted run must not leave a
# half-written "<web-root>.new" behind for the next one to mistake for a
# finished extraction.
trap 'rm -rf "$work_dir" ${staging:+"$staging"}' EXIT INT TERM

# --- Preparation. Everything that can fail happens here, before the first byte
# --- of the installation is touched, so a failed download or a bad checksum
# --- leaves the unit exactly as it was.

if [ $do_plugin -eq 1 ]; then
    if [ ! -f "$plugin_src" ]; then
        echo "install-on-pi.sh: plugin source not found: $plugin_src" >&2
        exit 1
    fi
    if [ -z "$plugins_dir" ]; then
        if [ -f "$config_file" ]; then
            plugins_dir=$(read_custom_plugins "$config_file")
            if [ -n "$plugins_dir" ]; then
                require_clean_dir_value "main.custom_plugins in $config_file" "$plugins_dir"
                echo "Plugins dir:    $plugins_dir (main.custom_plugins in $config_file)"
            fi
        fi
        defaults_glob=""
        defaults_list=""
        defaults_count=0
        if [ -z "$plugins_dir" ]; then
            # Upstream's own default moved once already, between the two
            # pwnagotchi versions this project targets (issue #157). Carrying
            # either value as a literal is right on one version and silently
            # wrong on the other, so instead of guessing, this reads
            # custom_plugins out of the defaults.toml actually shipped on this
            # unit, under the image's Python tree (F26, SPEC.md 5.3.1), with
            # the same parser the user configuration goes through. The Python
            # version is part of that path and has moved before too, so it is
            # globbed rather than pinned to one release.
            defaults_glob="$pwn_prefix/lib/python*/site-packages/pwnagotchi/defaults.toml"
            for defaults_file in $defaults_glob; do
                [ -f "$defaults_file" ] || continue
                defaults_list="$defaults_list $defaults_file"
                defaults_count=$((defaults_count + 1))
            done
            if [ "$defaults_count" -eq 1 ]; then
                defaults_file=$(trim "$defaults_list")
                plugins_dir=$(read_custom_plugins "$defaults_file")
                if [ -n "$plugins_dir" ]; then
                    require_clean_dir_value "custom_plugins in $defaults_file" "$plugins_dir"
                    echo "Plugins dir:    $plugins_dir (custom_plugins in $defaults_file)"
                fi
            elif [ "$defaults_count" -gt 1 ]; then
                # More than one Python tree under $pwn_prefix is a unit in a
                # state this script has never seen, most likely a partial
                # upgrade, and picking one of them is exactly the kind of
                # silent wrong answer this fallback exists to avoid. Refuse
                # rather than guess.
                echo "install-on-pi.sh: more than one defaults.toml matches $defaults_glob:" >&2
                for defaults_file in $defaults_list; do
                    echo "install-on-pi.sh:   $defaults_file" >&2
                done
                echo "install-on-pi.sh: pass --plugins-dir to name the directory directly" >&2
                exit 1
            fi
        fi
        if [ -z "$plugins_dir" ]; then
            # No fallback beyond this (SPEC.md 5.3.1): a wrong directory here
            # is invisible, every file is written, the script exits 0, and the
            # plugin never loads. Refusing is louder and no more inconvenient,
            # since the fix is one flag.
            echo "install-on-pi.sh: could not resolve the custom plugins directory" >&2
            if [ -f "$config_file" ]; then
                echo "install-on-pi.sh:   $config_file sets no main.custom_plugins" >&2
            else
                echo "install-on-pi.sh:   no $config_file" >&2
            fi
            if [ "$defaults_count" -eq 0 ]; then
                echo "install-on-pi.sh:   no defaults.toml matches $defaults_glob" >&2
            else
                echo "install-on-pi.sh:   $defaults_file sets no custom_plugins" >&2
            fi
            echo "install-on-pi.sh:   --plugins-dir was not given" >&2
            exit 1
        fi
    else
        echo "Plugins dir:    $plugins_dir (--plugins-dir)"
    fi
fi

if [ $do_web -eq 1 ]; then
    if [ -n "$archive" ]; then
        if [ ! -f "$archive" ]; then
            echo "install-on-pi.sh: archive not found: $archive" >&2
            exit 1
        fi
        echo "Archive:        $archive"
        # A local archive is verified against a SHA256SUMS sitting beside it,
        # exactly as a downloaded one is, and there is deliberately no way to
        # skip it: --archive is how someone installs a build they did not make
        # themselves, so it is the path that needs the check most, not least.
        case "$archive" in
            */*) sums="${archive%/*}/SHA256SUMS" ;;
            *) sums="SHA256SUMS" ;;
        esac
        if [ ! -f "$sums" ]; then
            echo "install-on-pi.sh: no $sums beside the archive" >&2
            echo "  an archive that cannot be verified is not installed" >&2
            exit 1
        fi
        verify_checksum "$archive" "$sums" || exit 1
        verify_provenance "$archive" local || exit 1
    else
        if [ -z "$tag" ]; then
            if ! curl -fsSL -o "$work_dir/release.json" \
                "https://api.github.com/repos/$repo/releases/latest" \
                2> "$work_dir/curl.log"; then
                cat "$work_dir/curl.log" >&2
                echo "install-on-pi.sh: no published release found for $repo" >&2
                exit 1
            fi
            tag=$(read_tag_name "$work_dir/release.json")
            if [ -z "$tag" ]; then
                echo "install-on-pi.sh: no published release found for $repo" >&2
                exit 1
            fi
        fi
        echo "Release:        $repo $tag"
        base="https://github.com/$repo/releases/download/$tag"
        archive="$work_dir/dist.tgz"
        sums="$work_dir/SHA256SUMS"
        if ! curl -fsSL -o "$archive" "$base/dist.tgz" 2> "$work_dir/curl.log"; then
            cat "$work_dir/curl.log" >&2
            echo "install-on-pi.sh: could not download $base/dist.tgz" >&2
            exit 1
        fi
        if ! curl -fsSL -o "$sums" "$base/SHA256SUMS" 2> "$work_dir/curl.log"; then
            cat "$work_dir/curl.log" >&2
            echo "install-on-pi.sh: could not download $base/SHA256SUMS" >&2
            exit 1
        fi
        verify_checksum "$archive" "$sums" || exit 1
        verify_provenance "$archive" release || exit 1
    fi

    check_archive_paths "$archive" || exit 1

    # Extraction goes beside the target so the swap is a rename on the same
    # filesystem and an interrupted unpack never leaves a half-written web root.
    # A dry run stages under the temp directory instead: same extraction, same
    # checks, same failure modes, but nothing appears next to the real web root.
    if [ $dry_run -eq 1 ]; then
        staging="$work_dir/staging"
    else
        staging="$web_root.new"
        case "$web_root" in
            */*) mkdir -p "${web_root%/*}" ;;
        esac
    fi
    rm -rf "$staging"
    mkdir -p "$staging"
    # Extracting as root would otherwise restore whatever ownership and mode
    # bits the archive recorded, setuid included. The modes the installed tree
    # needs are decided here, not by whoever packed the tarball.
    if ! tar -xzf "$archive" -C "$staging" \
        --no-same-owner --no-same-permissions 2> "$work_dir/tar.log"; then
        cat "$work_dir/tar.log" >&2
        echo "install-on-pi.sh: could not unpack $archive" >&2
        exit 1
    fi
    # SPEC 5.3.1 pins these modes, so a failure to set them is a failed install
    # rather than a detail: stderr is not discarded. `|| true` only absorbs the
    # exit status find returns when the tree is empty, which set -e would
    # otherwise treat as fatal.
    find "$staging" -type d -exec chmod 0755 {} + || true
    find "$staging" -type f -exec chmod 0644 {} + || true
fi

# --- Installation. From here on the only remaining failures are the filesystem
# --- refusing a rename.

if [ $do_plugin -eq 1 ]; then
    if [ $dry_run -eq 1 ]; then
        echo "dry run: would install $plugin_src as ${plugins_dir%/}/companion.py"
    else
        mkdir -p "$plugins_dir"
        # Written under a temporary name in the destination directory and moved
        # into place, so a plugin scan that runs mid-copy never sees a truncated
        # file.
        cp "$plugin_src" "${plugins_dir%/}/.companion.py.new"
        chmod 0644 "${plugins_dir%/}/.companion.py.new"
        mv -f "${plugins_dir%/}/.companion.py.new" "${plugins_dir%/}/companion.py"
        echo "Plugin:         ${plugins_dir%/}/companion.py"
    fi
fi

if [ $do_web -eq 1 ]; then
    if [ $dry_run -eq 1 ]; then
        echo "dry run: would install the PWA into $web_root"
        [ ! -d "$web_root" ] || echo "dry run: would keep the current one as $web_root.previous"
    else
        # The previous web root is moved aside, never deleted before the
        # replacement is in place: the first thing anyone wants after a bad
        # update is the version that worked.
        if [ -e "$web_root" ]; then
            rm -rf "$web_root.previous"
            mv "$web_root" "$web_root.previous"
            echo "Previous:       $web_root.previous"
        fi
        mv "$staging" "$web_root"
        staging=""
        echo "Web root:       $web_root"
    fi
fi

if [ $dry_run -eq 1 ]; then
    echo "dry run: nothing was written"
elif [ $do_plugin -eq 1 ]; then
    echo "Enable the plugin in $config_file, then restart pwnagotchi."
fi
