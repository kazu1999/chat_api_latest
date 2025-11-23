#!/bin/bash
# Package Lambda function with dependencies for deployment
# Usage: ./package_realtime.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAMBDA_DIR="$SCRIPT_DIR/../lambda/ueki_realtime"
BUILD_DIR="$SCRIPT_DIR/.build"
PACKAGE_DIR="$BUILD_DIR/ueki_realtime_package"

echo "Packaging ueki_realtime Lambda function..."

# Create build directory
mkdir -p "$BUILD_DIR"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

# Copy handler and requirements
cp "$LAMBDA_DIR/handler.py" "$PACKAGE_DIR/"
cp "$LAMBDA_DIR/requirements.txt" "$PACKAGE_DIR/"

# Install dependencies
echo "Installing dependencies..."
pip install -r "$LAMBDA_DIR/requirements.txt" -t "$PACKAGE_DIR" --platform manylinux2014_x86_64 --only-binary=:all: --upgrade

# Remove unnecessary files to reduce package size
find "$PACKAGE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$PACKAGE_DIR" -type f -name "*.pyo" -delete 2>/dev/null || true

# Create ZIP file
cd "$PACKAGE_DIR"
zip -r "$BUILD_DIR/ueki_realtime.zip" . -q
cd "$SCRIPT_DIR"

echo "Package created: $BUILD_DIR/ueki_realtime.zip"
echo "Package size: $(du -h "$BUILD_DIR/ueki_realtime.zip" | cut -f1)"
