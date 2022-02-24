#!/bin/sh
COMPONENTS=pretix/pretix-plugin-bitpay
DIR=pretix_bitpay/locale
# Renerates .po files used for translating the plugin
set -e
set -x

# Lock Weblate
for c in $COMPONENTS; do
	wlc lock $c;
done

# Push changes from Weblate to GitHub
for c in $COMPONENTS; do
	wlc commit $c;
done

# Pull changes from GitHub
git pull --rebase

# Update po files itself
make localegen

# Commit changes
git add $DIR/*/*/*.po
git add $DIR/*.pot

git commit -s -m "Update po files
[CI skip]"

# Push changes
git push

# Unlock Weblate
for c in $COMPONENTS; do
	wlc unlock $c;
done
