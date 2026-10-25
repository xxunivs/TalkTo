#!/usr/bin/env bash
set -e

git clone https://github.com/flutter/flutter.git -b stable --depth 1 ./flutter
export PATH="$PATH:$(pwd)/flutter/bin"
export FLUTTER_ALLOW_ROOT=true

flutter config --enable-web
flutter pub get
flutter build web --release