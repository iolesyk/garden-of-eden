#!/bin/bash

# Garden of Eden Service Management Script
# This script provides easy commands to manage the service

SERVICE_NAME="gardyn"

# Function to show usage
show_usage() {
    echo "🌱 Gardyn Service Manager"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  start     - Start the service"
    echo "  stop      - Stop the service"
    echo "  restart   - Restart the service"
    echo "  status    - Show service status"
    echo "  logs      - Show service logs (follow mode)"
    echo "  logs-all  - Show all service logs"
    echo "  enable    - Enable auto-start on boot"
    echo "  disable   - Disable auto-start on boot"
    echo "  install   - Install and setup the service"
    echo ""
}

# Function to check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "❌ This command requires root privileges. Use sudo:"
        echo "  sudo $0 $1"
        exit 1
    fi
}

# Main command handling
case "$1" in
    "start")
        check_root
        echo "🚀 Starting Gardyn service..."
        systemctl start "$SERVICE_NAME"
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    "stop")
        check_root
        echo "🛑 Stopping Gardyn service..."
        systemctl stop "$SERVICE_NAME"
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    "restart")
        check_root
        echo "🔄 Restarting Gardyn service..."
        systemctl restart "$SERVICE_NAME"
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    "status")
        echo "📊 Gardyn service status:"
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    "logs")
        echo "📋 Following Gardyn service logs (Ctrl+C to exit):"
        journalctl -u "$SERVICE_NAME" -f
        ;;
    "logs-all")
        echo "📋 All Gardyn service logs:"
        journalctl -u "$SERVICE_NAME"
        ;;
    "enable")
        check_root
        echo "🚀 Enabling Gardyn service to start on boot..."
        systemctl enable "$SERVICE_NAME"
        echo "✅ Service enabled for auto-start on boot"
        ;;
    "disable")
        check_root
        echo "⏹️  Disabling Gardyn service auto-start..."
        systemctl disable "$SERVICE_NAME"
        echo "✅ Service disabled from auto-start on boot"
        ;;
    "install")
        check_root
        echo "🔧 Installing Gardyn service..."
        if [ -f "./setup-service.sh" ]; then
            ./setup-service.sh
        else
            echo "❌ setup-service.sh not found in current directory"
            exit 1
        fi
        ;;
    *)
        show_usage
        ;;
esac
