// Stitch Academic Paper Studio Client-Side App Controller
document.addEventListener("DOMContentLoaded", () => {
    console.log("Stitch Design System Client initialized");

    // Инжектим обработку навигации по шагам
    const stepPaths = {
        "1. Rules": "/step/1",
        "2. Sources": "/step/2",
        "3. Title": "/step/3",
        "4. Plan": "/step/4",
        "5. Studio": "/step/5",
        "6. Build": "/step/6"
    };

    const navItems = document.querySelectorAll("nav a");
    navItems.forEach((item) => {
        const text = item.textContent.trim();
        for (const [key, path] of Object.entries(stepPaths)) {
            if (text.includes(key)) {
                item.style.cursor = "pointer";
                item.addEventListener("click", (e) => {
                    e.preventDefault();
                    window.location.href = path;
                });
            }
        }
    });

    // Обработка кнопки генерации плана на Шаге 4
    const btnGenPlan = document.querySelector("#btn-gen-plan, button:contains('Сгенерировать'), button:contains('Plan')");
    if (btnGenPlan) {
        btnGenPlan.addEventListener("click", async () => {
            btnGenPlan.innerText = "⚡ ИИ генерирует план...";
            try {
                const res = await fetch("/api/generate-plan", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ topic: "Разработка приложения платежного терминала на C#" })
                });
                const data = await res.json();
                alert("План успешно сформирован ИИ! Перейдите на Шаг 5 (Студия).");
                window.location.href = "/step/5";
            } catch (err) {
                console.error(err);
                alert("Ошибка генерации плана: " + err);
            }
        });
    }

    // Обработка кнопки Сборки на Шаге 6
    const btnBuild = document.querySelector("#btn-build, button:contains('Build'), button:contains('Сформировать')");
    if (btnBuild) {
        btnBuild.addEventListener("click", async () => {
            btnBuild.innerText = "🚀 Формирование .docx в MS Word...";
            try {
                const res = await fetch("/api/build-docx", { method: "POST" });
                const data = await res.json();
                alert("Успешно! Документ отформатирован по ГОСТу и открывается в MS Word!");
            } catch (err) {
                console.error(err);
                alert("Ошибка сборки файла: " + err);
            }
        });
    }
});
