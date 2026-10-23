# 云端部署

当前采用Ubuntu 24.04、2核CPU、4 GB内存和50 GB磁盘，Python 3.11虚拟环境运行，未使用Docker。

1. 将项目放到 `/opt/materials-assistant`，按README安装依赖，准备Core索引、MP快照及 `.env`。
2. 将目录权限交给服务运行用户。示例服务使用 `ubuntu`，其他系统需要修改 `deploy/systemd/` 中的User和Group。
3. 复制服务并启用：

```bash
sudo cp deploy/systemd/materials-*.service /etc/systemd/system/
sudo chmod 600 /opt/materials-assistant/.env
sudo systemctl daemon-reload
sudo systemctl enable --now materials-api materials-ui
```

安装Caddy后，将 `deploy/Caddyfile.ip` 放到 `/etc/caddy/Caddyfile`，重载Caddy，防火墙放行80端口。API监听127.0.0.1:8006，前端监听127.0.0.1:8507，由Caddy对外提供入口。使用域名时可参考 `deploy/Caddyfile.example` 配置HTTPS。

```bash
sudo systemctl status materials-api materials-ui caddy
curl http://127.0.0.1:8006/staging
sudo journalctl -u materials-api -n 50 --no-pager
```

会话历史位于 `data/chat_history/`，工具会话位于 `data/chat_sessions/`。更新前备份这两个目录和 `.env`。浏览器随机识别码用于恢复历史，不需要注册账号。
