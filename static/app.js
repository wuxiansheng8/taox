// static/app.js
const { createApp, ref, onMounted, nextTick, watch } = Vue;

createApp({
    setup() {
        const authenticated = ref(false);
        const loading = ref(false);
        const loginError = ref("");
        
        const loginForm = ref({
            username: "",
            password: ""
        });

        // 导航 Tab 控制
        const currentTab = ref("dashboard");

        //看板数据
        const is_running = ref(false);
        const next_scan_time = ref(null);
        const countdownText = ref("00秒");
        const active_accounts_count = ref(0);
        const tg_configured = ref(false);
        const last_error = ref(null);
        let chartInstance = null;

        // 监控别名管理
        const remarksList = ref([]);
        const remarksCount = ref(0);
        const editRemarkMode = ref(false);
        const remarkForm = ref({
            username: "",
            remark_name: "",
            is_highlight: 0
        });

        // 账号小号配置
        const accountsList = ref([]);
        const testingAccount = ref(false);
        const accountForm = ref({
            username: "",
            password: "",
            email: "",
            proxy: "",
            remark: ""
        });

        // 全局配置与 AI 设置
        const settings = ref({
            admin_username: "admin",
            admin_password: "",
            tg_token: "",
            tg_chat_id: "",
            polling_interval: 10,
            twitter_list_id: "",
            translate_enabled: 0,
            translate_provider_primary: "google",
            translate_provider_backup: "google",
            openai_primary_api_key: "",
            openai_primary_base_url: "",
            openai_primary_model: "",
            openai_backup_api_key: "",
            openai_backup_base_url: "",
            openai_backup_model: ""
        });
        const testingTG = ref(false);

        // 运行日志
        const logs = ref([]);
        const logContainer = ref(null);

        // 定时轮询器 ID 引用
        let dashboardIntervalId = null;
        let countdownIntervalId = null;
        let logEventSource = null;

        // --- HTTP 辅助请求方法 ---
        async function apiRequest(url, method = "GET", body = null, isJson = true) {
            const options = { method };
            if (body) {
                if (isJson) {
                    options.headers = { "Content-Type": "application/json" };
                    options.body = JSON.stringify(body);
                } else {
                    options.body = body; // 比如文件上传 UploadFile
                }
            }
            const response = await fetch(url, options);
            if (response.status === 401) {
                authenticated.value = false;
                stopAllPolling();
                throw new Error("会话已过期，请重新登录");
            }
            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || `请求失败，状态码: ${response.status}`);
            }
            return response;
        }

        // --- 鉴权管理 ---
        async function checkAuth() {
            try {
                const resp = await fetch("/api/auth/check");
                const data = await resp.json();
                if (data.authenticated) {
                    authenticated.value = true;
                    startDashboardPolling();
                    fetchRemarks();
                    fetchAccounts();
                    fetchSettings();
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function handleLogin() {
            loginError.value = "";
            if (!loginForm.value.username || !loginForm.value.password) {
                loginError.value = "请完整输入用户名和密码";
                return;
            }
            loading.value = true;
            try {
                const response = await fetch("/api/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(loginForm.value)
                });
                
                if (response.ok) {
                    authenticated.value = true;
                    loginForm.value.password = "";
                    startDashboardPolling();
                    fetchRemarks();
                    fetchAccounts();
                    fetchSettings();
                    setTab("dashboard");
                } else {
                    const data = await response.json();
                    loginError.value = data.detail || "登录验证失败";
                }
            } catch (e) {
                loginError.value = "网络连接故障，无法连接服务器";
            } finally {
                loading.value = false;
            }
        }

        async function handleLogout() {
            try {
                await fetch("/api/auth/logout", { method: "POST" });
            } catch (e) {}
            authenticated.value = false;
            stopAllPolling();
        }

        // --- 倒计时计算逻辑 ---
        function startCountdown() {
            if (countdownIntervalId) clearInterval(countdownIntervalId);
            countdownIntervalId = setInterval(() => {
                if (!next_scan_time.value || !is_running.value) {
                    countdownText.value = "已暂停";
                    return;
                }
                const now = new Date();
                const next = new Date(next_scan_time.value);
                const diff = Math.max(0, Math.floor((next - now) / 1000));
                countdownText.value = `${diff} 秒`;
            }, 1000);
        }

        // --- 看板数据获取与图表绘制 ---
        async function fetchDashboardData() {
            try {
                const resp = await apiRequest("/api/dashboard/metrics");
                const data = await resp.json();
                
                is_running.value = data.is_running;
                next_scan_time.value = data.next_scan_time;
                active_accounts_count.value = data.active_accounts_count;
                tg_configured.value = data.tg_configured;
                last_error.value = data.last_error;

                // 异步绘制折线图看板
                await nextTick();
                drawChart(data.chart_data);
            } catch (e) {
                console.error(e);
            }
        }

        function drawChart(chartData) {
            const ctx = document.getElementById("metricsChart");
            if (!ctx) return;
            
            if (chartInstance) {
                chartInstance.data.labels = chartData.labels;
                chartInstance.data.datasets[0].data = chartData.success;
                chartInstance.data.datasets[1].data = chartData.failure;
                chartInstance.update();
                return;
            }

            chartInstance = new Chart(ctx.getContext("2d"), {
                type: "line",
                data: {
                    labels: chartData.labels,
                    datasets: [
                        {
                            label: "轮询成功 (次)",
                            data: chartData.success,
                            borderColor: "#48bb78",
                            backgroundColor: "rgba(72, 187, 120, 0.1)",
                            tension: 0.3,
                            fill: true
                        },
                        {
                            label: "轮询失败 (次)",
                            data: chartData.failure,
                            borderColor: "#f56565",
                            backgroundColor: "rgba(245, 101, 101, 0.1)",
                            tension: 0.3,
                            fill: true
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: "rgba(255,255,255,0.05)" },
                            ticks: { color: "#a0aec0" }
                        },
                        x: {
                            grid: { display: false },
                            ticks: { color: "#a0aec0" }
                        }
                    },
                    plugins: {
                        legend: { labels: { color: "#f7fafc" } }
                    }
                }
            });
        }

        async function toggleScheduler() {
            const action = is_running.value ? "stop" : "start";
            try {
                const resp = await apiRequest(`/api/settings/toggle-scheduler?action=${action}`, "POST");
                const data = await resp.json();
                alert(data.message);
                fetchDashboardData();
            } catch (e) {
                alert(e.message);
            }
        }

        // --- 监控备注 API 操作 ---
        async function fetchRemarks() {
            try {
                const resp = await apiRequest("/api/remarks");
                const data = await resp.json();
                remarksList.value = data.remarks;
                remarksCount.value = data.count;
            } catch (e) {
                console.error(e.message);
            }
        }

        async function saveRemark() {
            if (!remarkForm.value.username || !remarkForm.value.remark_name) {
                alert("用户名与备注名不能为空");
                return;
            }
            try {
                await apiRequest("/api/remarks", "POST", remarkForm.value);
                alert("备注保存成功！");
                remarkForm.value = { username: "", remark_name: "", is_highlight: 0 };
                editRemarkMode.value = false;
                fetchRemarks();
            } catch (e) {
                alert(e.message);
            }
        }

        function startEditRemark(remark) {
            editRemarkMode.value = true;
            remarkForm.value = {
                username: remark.username,
                remark_name: remark.remark_name,
                is_highlight: remark.is_highlight
            };
        }

        function cancelEditRemark() {
            editRemarkMode.value = false;
            remarkForm.value = { username: "", remark_name: "", is_highlight: 0 };
        }

        async function deleteRemark(username) {
            if (!confirm(`确定要删除对用户 @${username} 的备注配置吗？`)) return;
            try {
                await apiRequest(`/api/remarks/${username}`, "DELETE");
                fetchRemarks();
            } catch (e) {
                alert(e.message);
            }
        }

        // TXT 文件模版、导出、导入
        function downloadTemplate() {
            window.open("/api/remarks/template", "_blank");
        }

        function exportRemarks() {
            window.open("/api/remarks/export", "_blank");
        }

        function triggerImport() {
            document.getElementById("importFile").click();
        }

        async function handleImport(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append("file", file);
            
            try {
                const resp = await apiRequest("/api/remarks/import", "POST", formData, false);
                const data = await resp.json();
                alert(data.message);
                fetchRemarks();
            } catch (e) {
                alert(e.message);
            } finally {
                event.target.value = ""; // 清空选中状态
            }
        }

        // --- 推特账号及代理 API 操作 ---
        async function fetchAccounts() {
            try {
                const resp = await apiRequest("/api/accounts");
                accountsList.value = await resp.json();
            } catch (e) {
                console.error(e.message);
            }
        }

        async function saveAccount() {
            if (!accountForm.value.username || !accountForm.value.password || !accountForm.value.email || !accountForm.value.proxy) {
                alert("为了确保账号不关联被封，添加账号时用户名、密码、绑定的邮箱、独立的代理 IP 均为必填项！");
                return;
            }
            testingAccount.value = true;
            try {
                const resp = await apiRequest("/api/accounts", "POST", accountForm.value);
                const data = await resp.json();
                alert(data.message);
                // 成功后清空表单
                accountForm.value = { username: "", password: "", email: "", proxy: "", remark: "" };
                fetchAccounts();
                fetchDashboardData();
            } catch (e) {
                alert(`联调测试不通过：\n${e.message}\n\n该配置无法保存，请修正后重试。`);
            } finally {
                testingAccount.value = false;
            }
        }

        async function deleteAccount(username) {
            if (!confirm(`确定要删除推特账号 @${username} 吗？`)) return;
            try {
                await apiRequest(`/api/accounts/${username}`, "DELETE");
                fetchAccounts();
                fetchDashboardData();
            } catch (e) {
                alert(e.message);
            }
        }

        // --- 全局配置与 AI 设置 ---
        async function fetchSettings() {
            try {
                const resp = await apiRequest("/api/settings");
                const data = await resp.json();
                // 转换为 Pydantic 定义对应的整型/字符串数据
                settings.value = {
                    ...data,
                    polling_interval: parseInt(data.polling_interval || 10),
                    translate_enabled: parseInt(data.translate_enabled || 0),
                    admin_password: ""
                };
            } catch (e) {
                console.error(e.message);
            }
        }

        async function saveSettings() {
            try {
                const resp = await apiRequest("/api/settings", "POST", settings.value);
                const data = await resp.json();
                alert(data.message);
                settings.value.admin_password = ""; // 清空输入的密码
                fetchSettings();
                fetchDashboardData();
            } catch (e) {
                alert(e.message);
            }
        }

        async function testTG() {
            if (!settings.value.tg_token || !settings.value.tg_chat_id) {
                alert("请先输入 Token 和 Chat ID。");
                return;
            }
            testingTG.value = true;
            try {
                const resp = await apiRequest("/api/settings/test-tg", "POST", {
                    token: settings.value.tg_token,
                    chat_id: settings.value.tg_chat_id
                });
                const data = await resp.json();
                alert(data.message);
            } catch (e) {
                alert(e.message);
            } finally {
                testingTG.value = false;
            }
        }

        async function testTranslation(channel) {
            try {
                const resp = await apiRequest("/api/settings/test-translation", "POST", { channel });
                const data = await resp.json();
                alert(data.message);
            } catch (e) {
                alert(e.message);
            }
        }

        async function checkBalance(channel) {
            try {
                const resp = await apiRequest("/api/settings/check-balance", "POST", { channel });
                const data = await resp.json();
                alert(data.balance);
            } catch (e) {
                alert(e.message);
            }
        }

        // --- 运行日志管理 ---
        async function fetchLogs() {
            try {
                const resp = await apiRequest("/api/logs?lines=200");
                const data = await resp.json();
                logs.value = data.logs;
                await nextTick();
                scrollLogsToBottom();
            } catch (e) {
                console.error(e.message);
            }
        }

        async function clearLogs() {
            if (!confirm("确定要清空服务器上的运行日志吗？")) return;
            try {
                await apiRequest("/api/logs", "DELETE");
                logs.value = [];
            } catch (e) {
                alert(e.message);
            }
        }

        function getLogClass(line) {
            if (line.includes("[ERROR]") || line.includes("failed")) return "log-error";
            if (line.includes("[WARNING]") || line.includes("warn")) return "log-warning";
            return "log-info";
        }

        function scrollLogsToBottom() {
            const container = logContainer.value;
            if (container) {
                container.scrollTop = container.scrollHeight;
            }
        }

        // --- 页面轮询控制 ---
        function setTab(tab) {
            currentTab.value = tab;
            
            // 切换日志 Tab 自动建立实时 SSE 连接
            if (tab === "logs") {
                logs.value = [];
                if (logEventSource) {
                    logEventSource.close();
                }
                
                logEventSource = new EventSource("/api/logs/stream");
                
                logEventSource.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        logs.value.push(data.log);
                        
                        // 前端展示最大限制在 1000 行，避免卡顿
                        if (logs.value.length > 1000) {
                            logs.value.shift();
                        }
                        nextTick(() => {
                            scrollLogsToBottom();
                        });
                    } catch (e) {
                        console.error("解析日志事件失败:", e);
                    }
                };
                
                logEventSource.onerror = (err) => {
                    console.error("日志流传输发生错误或连接中断:", err);
                    if (logEventSource) {
                        logEventSource.close();
                        logEventSource = null;
                    }
                };
            } else {
                if (logEventSource) {
                    logEventSource.close();
                    logEventSource = null;
                }
            }
        }

        function startDashboardPolling() {
            fetchDashboardData();
            startCountdown();
            if (dashboardIntervalId) clearInterval(dashboardIntervalId);
            dashboardIntervalId = setInterval(fetchDashboardData, 5000); // 5秒轮询一次看板指标
        }

        function stopAllPolling() {
            if (dashboardIntervalId) clearInterval(dashboardIntervalId);
            if (countdownIntervalId) clearInterval(countdownIntervalId);
            if (logEventSource) {
                logEventSource.close();
                logEventSource = null;
            }
            dashboardIntervalId = null;
            countdownIntervalId = null;
        }

        // 监听视图中日志容器，保证有新日志时能滚动触底
        watch(logs, () => {
            nextTick(scrollLogsToBottom);
        });

        onMounted(() => {
            checkAuth();
        });

        return {
            authenticated,
            loading,
            loginError,
            loginForm,
            currentTab,
            setTab,
            is_running,
            countdownText,
            active_accounts_count,
            tg_configured,
            last_error,
            toggleScheduler,
            remarksList,
            remarksCount,
            editRemarkMode,
            remarkForm,
            saveRemark,
            startEditRemark,
            cancelEditRemark,
            deleteRemark,
            downloadTemplate,
            exportRemarks,
            triggerImport,
            handleImport,
            accountsList,
            testingAccount,
            accountForm,
            saveAccount,
            deleteAccount,
            settings,
            testingTG,
            saveSettings,
            testTG,
            testTranslation,
            checkBalance,
            logs,
            logContainer,
            clearLogs,
            getLogClass,
            fetchLogs,
            handleLogin,
            handleLogout
        };
    }
}).mount("#app");
