#!/usr/bin/env bash
#
# bootstrap.sh — install the dependencies this repo needs.
#
#   ./bootstrap.sh cli       Python CLI (uv + AWS CLI v2)
#   ./bootstrap.sh android   Android build (JDK 17 + Android SDK)
#   ./bootstrap.sh all       both
#
# Supports macOS (arm64/x86_64) and Ubuntu (x86_64/aarch64).
# Idempotent: everything already present is left alone.
#
# Writes env.sh with the machine-specific paths. Source it before building:
#   source ./env.sh && (cd shield-vpn-companion/android && ./gradlew assembleDebug)
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pinned rather than "latest" so two machines resolve the same toolchain.
CMDLINE_TOOLS_VERSION="13114758"

# Driven by shield-vpn-companion/android: AGP 8.5.2, Gradle 8.7, compileSdk 34.
JDK_MAJOR="17"
ANDROID_PLATFORM="android-34"
ANDROID_BUILD_TOOLS="34.0.0"

info() { printf '\033[0;34m▶\033[0m %s\n' "$*"; }
ok() { printf '\033[0;32m✅\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m⚠\033[0m  %s\n' "$*" >&2; }
die() {
  printf '\033[0;31m❌\033[0m %s\n' "$*" >&2
  exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- platform

case "$(uname -s)" in
Darwin) PLATFORM="mac" ;;
Linux) PLATFORM="linux" ;;
*) die "unsupported OS: $(uname -s). Only macOS and Linux are supported." ;;
esac

ARCH="$(uname -m)"

case "$PLATFORM:$ARCH" in
mac:arm64 | mac:x86_64 | linux:x86_64 | linux:aarch64) ;;
*) die "unsupported platform: $PLATFORM/$ARCH" ;;
esac

if [ "$PLATFORM" = "mac" ]; then
  ANDROID_HOME_DEFAULT="$HOME/Library/Android/sdk"
  CMDLINE_TOOLS_OS="mac"
else
  ANDROID_HOME_DEFAULT="$HOME/Android/Sdk"
  CMDLINE_TOOLS_OS="linux"
fi

ANDROID_HOME="${ANDROID_HOME:-$ANDROID_HOME_DEFAULT}"
JAVA_HOME_RESOLVED=""

require_brew() {
  have brew && return 0
  die "Homebrew not found. Install it first: https://brew.sh"
}

require_sudo() {
  sudo -n true 2>/dev/null && return 0
  info "This step needs sudo; you may be prompted."
  sudo true || die "sudo is required to install system packages on Linux."
}

# --------------------------------------------------------------------- cli

install_uv() {
  if have uv; then
    ok "uv already installed ($(uv --version))"
    return
  fi
  info "Installing uv..."
  if [ "$PLATFORM" = "mac" ]; then
    require_brew
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  have uv || die "uv install finished but the binary is not on PATH."
  ok "uv installed ($(uv --version))"
}

install_aws() {
  # infra.py shells out to `aws lightsail`, and v1 is not compatible.
  if have aws && aws --version 2>&1 | grep -q 'aws-cli/2'; then
    ok "AWS CLI v2 already installed ($(aws --version 2>&1 | cut -d' ' -f1))"
    return
  fi
  if have aws; then
    warn "Found AWS CLI v1 ($(aws --version 2>&1 | cut -d' ' -f1)); v2 is required."
  fi

  info "Installing AWS CLI v2..."
  if [ "$PLATFORM" = "mac" ]; then
    require_brew
    brew install awscli
  else
    # apt only ships v1, so use the official installer.
    local zip_arch tmp
    case "$ARCH" in
    x86_64) zip_arch="x86_64" ;;
    aarch64) zip_arch="aarch64" ;;
    *) die "no AWS CLI v2 build for $ARCH" ;;
    esac
    require_sudo
    have unzip || { sudo apt-get update -qq && sudo apt-get install -y -qq unzip; }
    tmp="$(mktemp -d)"
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${zip_arch}.zip" -o "$tmp/awscliv2.zip"
    unzip -q "$tmp/awscliv2.zip" -d "$tmp"
    sudo "$tmp/aws/install" --update
    rm -rf "$tmp"
  fi
  have aws || die "AWS CLI install finished but the binary is not on PATH."
  ok "AWS CLI installed ($(aws --version 2>&1 | cut -d' ' -f1))"
}

sync_cli_deps() {
  [ -f "$REPO_DIR/cli/pyproject.toml" ] || {
    warn "cli/pyproject.toml not found; skipping dependency sync."
    return
  }
  info "Syncing Python dependencies..."
  (cd "$REPO_DIR/cli" && uv sync --quiet) || warn "uv sync failed; 'uv run' will resolve on first use."
  ok "Python dependencies ready"
}

# ----------------------------------------------------------------- android

# macOS `java_home -v 17` exits 0 and returns whatever JDK it has when 17 is
# absent, so the version is verified by running javac rather than trusted.
is_jdk_major() {
  local home="$1" want="$2" reported
  [ -x "$home/bin/javac" ] || return 1
  reported="$("$home/bin/javac" -version 2>&1 | grep -oE 'javac [0-9]+' | head -1)"
  [ "${reported#javac }" = "$want" ]
}

detect_java_home() {
  local candidate
  local -a candidates=()

  if [ "$PLATFORM" = "mac" ]; then
    candidate="$(/usr/libexec/java_home -v "$JDK_MAJOR" 2>/dev/null || true)"
    [ -n "$candidate" ] && candidates+=("$candidate")
    # Homebrew's openjdk is keg-only and invisible to java_home.
    if have brew; then
      candidate="$(brew --prefix "openjdk@$JDK_MAJOR" 2>/dev/null || true)"
      [ -n "$candidate" ] && candidates+=("$candidate/libexec/openjdk.jdk/Contents/Home")
    fi
  else
    for candidate in /usr/lib/jvm/java-"$JDK_MAJOR"-openjdk-*; do
      [ -d "$candidate" ] && candidates+=("$candidate")
    done
  fi

  for candidate in "${candidates[@]}"; do
    if is_jdk_major "$candidate" "$JDK_MAJOR"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_jdk() {
  if JAVA_HOME_RESOLVED="$(detect_java_home)"; then
    ok "JDK $JDK_MAJOR already installed ($JAVA_HOME_RESOLVED)"
    return
  fi

  info "Installing JDK $JDK_MAJOR (required by AGP 8.5.2 / Gradle 8.7)..."
  if [ "$PLATFORM" = "mac" ]; then
    require_brew
    # Keg-only, so it stays invisible to /usr/libexec/java_home. That is fine:
    # detect_java_home reads the keg directly and env.sh pins JAVA_HOME.
    brew install "openjdk@$JDK_MAJOR"
  else
    require_sudo
    sudo apt-get update -qq
    sudo apt-get install -y -qq "openjdk-$JDK_MAJOR-jdk"
  fi

  JAVA_HOME_RESOLVED="$(detect_java_home)" ||
    die "JDK $JDK_MAJOR installed but could not be located."
  ok "JDK $JDK_MAJOR installed ($JAVA_HOME_RESOLVED)"
}

install_cmdline_tools() {
  local sdkmanager="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
  if [ -x "$sdkmanager" ]; then
    ok "Android cmdline-tools already present ($ANDROID_HOME)"
    return
  fi

  info "Installing Android cmdline-tools into $ANDROID_HOME..."
  local tmp
  tmp="$(mktemp -d)"
  if [ "$PLATFORM" = "linux" ]; then
    have unzip || { require_sudo && sudo apt-get install -y -qq unzip; }
  fi
  curl -fsSL \
    "https://dl.google.com/android/repository/commandlinetools-${CMDLINE_TOOLS_OS}-${CMDLINE_TOOLS_VERSION}_latest.zip" \
    -o "$tmp/cmdline-tools.zip"
  unzip -q "$tmp/cmdline-tools.zip" -d "$tmp"
  mkdir -p "$ANDROID_HOME/cmdline-tools"
  rm -rf "$ANDROID_HOME/cmdline-tools/latest"
  mv "$tmp/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
  rm -rf "$tmp"

  [ -x "$sdkmanager" ] || die "cmdline-tools unpacked but sdkmanager is missing."
  ok "Android cmdline-tools installed"
}

sdk_packages_present() {
  [ -d "$ANDROID_HOME/platforms/$ANDROID_PLATFORM" ] &&
    [ -d "$ANDROID_HOME/build-tools/$ANDROID_BUILD_TOOLS" ] &&
    [ -x "$ANDROID_HOME/platform-tools/adb" ]
}

install_sdk_packages() {
  local sdkmanager="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
  export JAVA_HOME="$JAVA_HOME_RESOLVED"
  export ANDROID_HOME
  export ANDROID_SDK_ROOT="$ANDROID_HOME"

  if sdk_packages_present; then
    ok "Android SDK packages already present (platform-tools, $ANDROID_PLATFORM, $ANDROID_BUILD_TOOLS)"
    return
  fi

  info "Accepting SDK licenses..."
  yes 2>/dev/null | "$sdkmanager" --licenses >/dev/null || true

  info "Installing SDK packages: platform-tools, platforms;$ANDROID_PLATFORM, build-tools;$ANDROID_BUILD_TOOLS"
  "$sdkmanager" --install \
    "platform-tools" \
    "platforms;$ANDROID_PLATFORM" \
    "build-tools;$ANDROID_BUILD_TOOLS" >/dev/null ||
    die "sdkmanager failed to install the required packages."

  sdk_packages_present ||
    die "SDK packages still missing after install."
  ok "Android SDK packages ready"
}

# ------------------------------------------------------------------ env.sh

write_env_sh() {
  local env_file="$REPO_DIR/env.sh"
  info "Writing $env_file"

  # Single quotes below are deliberate: these variables must be written into
  # env.sh literally and expand when the user sources it, not right now.
  # shellcheck disable=SC2016
  {
    echo "# Generated by bootstrap.sh on $(date -u '+%Y-%m-%dT%H:%M:%SZ') for $PLATFORM/$ARCH."
    echo "# Machine-specific — not versioned. Re-run ./bootstrap.sh to regenerate."
    echo "# Put secrets and overrides in env.local.sh; this file never touches it."
    echo ""
    if [ -n "$JAVA_HOME_RESOLVED" ]; then
      echo "export JAVA_HOME=\"$JAVA_HOME_RESOLVED\""
      echo 'export PATH="$JAVA_HOME/bin:$PATH"'
    fi
    if [ -d "$ANDROID_HOME" ]; then
      echo "export ANDROID_HOME=\"$ANDROID_HOME\""
      echo 'export ANDROID_SDK_ROOT="$ANDROID_HOME"'
      echo 'export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"'
    fi
    if [ "$PLATFORM" = "linux" ] && [ -d "$HOME/.local/bin" ]; then
      echo 'export PATH="$HOME/.local/bin:$PATH"'
    fi
    echo ""
    echo '# Local overrides (AWS profile, SHIELD_VPN_TOKEN_*, ...)'
    echo '# shellcheck source=/dev/null'
    echo "[ -f \"$REPO_DIR/env.local.sh\" ] && . \"$REPO_DIR/env.local.sh\""
  } >"$env_file"

  ok "env.sh written — run 'source ./env.sh' before building"
}

# -------------------------------------------------------------------- main

do_cli() {
  info "=== CLI dependencies ($PLATFORM/$ARCH) ==="
  install_uv
  install_aws
  sync_cli_deps
}

do_android() {
  info "=== Android build dependencies ($PLATFORM/$ARCH) ==="
  install_jdk
  install_cmdline_tools
  install_sdk_packages
}

usage() {
  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

main() {
  case "${1:-}" in
  cli)
    do_cli
    # Resolve an existing JDK so env.sh stays useful, but do not install one.
    JAVA_HOME_RESOLVED="$(detect_java_home || true)"
    ;;
  android) do_android ;;
  all)
    do_cli
    do_android
    ;;
  -h | --help | help | "")
    usage
    exit 0
    ;;
  *)
    printf 'Unknown target: %s\n\n' "$1" >&2
    usage >&2
    exit 1
    ;;
  esac

  write_env_sh
  echo ""
  ok "Bootstrap complete."
}

main "$@"
