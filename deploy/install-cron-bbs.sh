#!/bin/bash
# ============================================================
#  行知教研吧 · 注册计划任务（开机自启 + 每 5 分钟守护）
#  这一步是「和校盘快传一模一样」的最后一块拼图。
#
#  为什么单独一个脚本：/etc/cron.d/ 只有 root 能写，
#  而首次启动（setup-bbs.sh）不需要 root，两件事分开更清楚。
#
#  用法（任选其一）：
#    echo '<你的密码>' | sudo -S bash ~/myproject/install-cron-bbs.sh
#    sudo bash ~/myproject/install-cron-bbs.sh
#
#  卸载： sudo rm -f /etc/cron.d/xingzhibbs
# ============================================================

set -u

MY=/volume2/homes/wza0126/myproject
CRON=/etc/cron.d/xingzhibbs     # 注意：/etc/cron.d 下的文件名不能带「.」，否则 crond 会忽略

if [ "$(id -u)" != "0" ]; then
    cat <<EOF
❌ 需要 root 权限才能写 $CRON

请用下面任一方式重跑：
    echo '<你的密码>' | sudo -S bash $MY/install-cron-bbs.sh
    sudo bash $MY/install-cron-bbs.sh
EOF
    exit 1
fi

if [ ! -x "$MY/watchdog-bbs.sh" ]; then
    echo "❌ 找不到可执行的 $MY/watchdog-bbs.sh"
    exit 1
fi

cat > "$CRON" <<EOF
# 行知教研吧（xgz-bbs）开机自启 + 每 5 分钟守护；端口 8009
# 以 wza0126 身份运行，保证数据库/附件属主和手动启动时一致
SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin
HOME=/volume2/homes/wza0126
MAILTO=""
@reboot      wza0126 bash $MY/watchdog-bbs.sh >> $MY/logs/watchdog-bbs.log 2>&1
*/5 * * * *  wza0126 bash $MY/watchdog-bbs.sh >> $MY/logs/watchdog-bbs.log 2>&1
EOF

chmod 644 "$CRON"
touch "$MY/logs/watchdog-bbs.log" 2>/dev/null
chown wza0126 "$MY/logs/watchdog-bbs.log" 2>/dev/null

echo "==> ✅ 已写入 $CRON"
cat "$CRON"
echo
echo "==> 立即按计划任务的方式试跑一次（应该幂等：8009 活着就什么都不做）"
su - wza0126 -c "bash $MY/watchdog-bbs.sh" 2>/dev/null || bash "$MY/watchdog-bbs.sh"
echo
echo "==> 当前 8009 监听情况"
netstat -tlnp 2>/dev/null | awk 'index($4, ":8009") > 0'
