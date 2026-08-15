#!/bin/sh
# Install the companion on a pwnagotchi: the plugin and the built PWA.
#
#   install-on-pi.sh [--web-root DIR] [--plugins-dir DIR] [--config FILE]
#                    [--tag vX.Y.Z] [--archive FILE] [--repo owner/name]
#                    [--plugin-only] [--web-only] [--dry-run]
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
# download, checksum, an archive that refuses to stay inside its target), 2
# usage error. Anything non-zero leaves the installation as it was.

set -eu

usage() {
    echo "usage: install-on-pi.sh [--web-root DIR] [--plugins-dir DIR] [--config FILE]" >&2
    echo "                        [--tag vX.Y.Z] [--archive FILE] [--repo owner/name]" >&2
    echo "                        [--plugin-only] [--web-only] [--dry-run]" >&2
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

web_root="/var/www/openpwn-companion"
plugins_dir=""
config_file="/etc/pwnagotchi/config.toml"
tag=""
archive=""
repo="abonforti/openpwnagotchi-companion"
# `repo` has a default, so its value cannot tell us whether the user named it.
repo_given=0
plugin_only=0
web_only=0
dry_run=0

while [ $# -gt 0 ]; do
    case "$1" in
        --web-root)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --web-root requires a directory" >&2
                usage
                exit 2
            fi
            # Absolute only. An empty value makes the staging directory `.new`
            # in whatever directory the script was started from, and that
            # directory is rm -rf'd on the way past.
            case "$2" in
                /*) ;;
                *)
                    echo "install-on-pi.sh: --web-root must be an absolute path: $2" >&2
                    exit 2
                    ;;
            esac
            web_root="$2"
            shift 2
            ;;
        --plugins-dir)
            if [ $# -lt 2 ]; then
                echo "install-on-pi.sh: --plugins-dir requires a directory" >&2
                usage
                exit 2
            fi
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
                echo "Plugins dir:    $plugins_dir (main.custom_plugins in $config_file)"
            else
                echo "Plugins dir:    $config_file sets no main.custom_plugins, using the default"
            fi
        else
            echo "Plugins dir:    no $config_file, using the default"
        fi
        # The defaults.toml value. The shipped shell profile aliases "custom" to
        # a different directory, so the two paths on the image disagree and a
        # hardcoded guess picks the wrong one on some units, where the plugin
        # then silently never loads.
        [ -n "$plugins_dir" ] || plugins_dir="/usr/local/share/pwnagotchi/custom-plugins/"
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
