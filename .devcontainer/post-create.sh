#!/bin/bash
set -e

echo "" >> $HOME/.bashrc
echo 'source <(just --completions bash)' >> $HOME/.bashrc
echo "" >> $HOME/.bashrc

# (cd src/batcher && npm ginstall)
pip install -r src/batcher/requirements.txt
