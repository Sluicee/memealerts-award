"""Desktop views: collection, reward settings and account setup."""
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QStackedWidget, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .rewards import LIMIT_MAXIMA, normalize_limits

STATES = {"pending": "В очереди", "sending": "Начисление", "uncertain": "Нужна проверка",
          "credited": "Ожидает Twitch", "done": "Начислено", "cancel_pending": "Возврат баллов",
          "canceled": "Баллы возвращены"}


def icon(active=False):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#79b8ff"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
    painter.setPen(QColor("#142133"))
    painter.setFont(QFont("Sans Serif", 29, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "m")
    if active:
        painter.setPen(QColor("#142133"))
        painter.setBrush(QColor("#78d5b0"))
        painter.drawEllipse(45, 45, 17, 17)
    painter.end()
    return QIcon(pixmap)


def label(text="", role="", wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback=None, role=""):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if role:
        widget.setObjectName(role)
    if callback:
        widget.clicked.connect(callback)
    return widget


def surface(role="surface"):
    frame = QFrame()
    frame.setObjectName(role)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    return frame, layout


def form_row(title, field, hint=""):
    wrapper = QWidget()
    wrapper.setObjectName("formField")
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    caption = label(title, "fieldLabel")
    caption.setBuddy(field)
    layout.addWidget(caption)
    layout.addWidget(field)
    if hint:
        layout.addWidget(label(hint, "muted", True))
    return wrapper


class SettingSpinBox(QSpinBox):
    """Scrolling a settings page must not silently change a price or a limit."""
    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class Window(QMainWindow):
    def __init__(self, worker):
        super().__init__()
        self.worker, self.quitting, self.active = worker, False, False
        self.has_reward = self.connected = self.busy = False
        self.loaded_reward = None
        self.saved_settings = {}
        self.rows = []
        self.last_status = "Подключите аккаунты, чтобы начать."
        self.warning = self.pending_command = ""
        self.setWindowTitle("MemeAlerts Award")
        self.setWindowIcon(icon())
        self.resize(1140, 800)
        self.setMinimumSize(880, 600)
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(178)
        nav_layout = QVBoxLayout(sidebar)
        nav_layout.setContentsMargins(18, 25, 18, 18)
        nav_layout.setSpacing(7)
        nav_layout.addWidget(label("m / award", "brand"))
        nav_layout.addWidget(label("Twitch + MemeAlerts", "muted"))
        nav_layout.addSpacing(30)
        self.navigation = QButtonGroup(self)
        self.nav_buttons = []
        for index, title in enumerate(("Сбор наград", "Награда", "Подключения")):
            nav = button(title, role="nav")
            nav.setCheckable(True)
            self.navigation.addButton(nav, index)
            nav.clicked.connect(lambda checked=False, page=index: self.change_page(page))
            self.nav_buttons.append(nav)
            nav_layout.addWidget(nav)
        nav_layout.addStretch()
        self.connection_indicator = label("Аккаунты\nне подключены", "muted", True)
        nav_layout.addWidget(self.connection_indicator)
        nav_layout.addSpacing(16)
        tray_btn = button("Свернуть в трей", self.minimize_to_tray, "quiet")
        nav_layout.addWidget(tray_btn)
        self.exit_btn = button("Завершить работу", self.quit, "quiet")
        nav_layout.addWidget(self.exit_btn)
        sidebar.setFixedWidth(max(190, *(widget.sizeHint().width() + 36
                                        for widget in [self.exit_btn, tray_btn, *self.nav_buttons])))
        outer.addWidget(sidebar)
        workspace = QWidget()
        work = QVBoxLayout(workspace)
        work.setContentsMargins(26, 24, 26, 18)
        work.setSpacing(16)
        header = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(5)
        self.page_title = label("Сбор наград", "title")
        self.page_subtitle = label("Управляйте обменом баллов канала на мемкоины.", "muted", True)
        headings.addWidget(self.page_title)
        headings.addWidget(self.page_subtitle)
        header.addLayout(headings, 1)
        self.badge = label("Сбор выключен", "badge")
        header.addWidget(self.badge)
        self.toggle = button("Включить сбор", self.toggle_collection, "primary")
        self.toggle.setMinimumWidth(160)
        self.toggle.setEnabled(False)
        header.addWidget(self.toggle)
        work.addLayout(header)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.collection_page())
        self.pages.addWidget(self.reward_page())
        self.pages.addWidget(self.connections_page())
        work.addWidget(self.pages, 1)
        self.notice_frame, notice_layout = surface("notice")
        notice_layout.setContentsMargins(14, 11, 14, 11)
        notice_row = QHBoxLayout()
        self.status = label(self.last_status, wrap=True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notice_row.addWidget(self.status, 1)
        self.dismiss_notice = button("Скрыть", self.clear_warning, "quiet")
        self.dismiss_notice.hide()
        notice_row.addWidget(self.dismiss_notice)
        notice_layout.addLayout(notice_row)
        work.addWidget(self.notice_frame)
        outer.addWidget(workspace, 1)
        self.tray = QSystemTrayIcon(icon(), self)
        menu = QMenu()
        show = QAction("Открыть MemeAlerts Award", self)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        self.tray_toggle = QAction("Включить сбор", self)
        self.tray_toggle.setEnabled(False)
        self.tray_toggle.triggered.connect(self.toggle_collection)
        menu.addAction(self.tray_toggle)
        menu.addSeparator()
        exit_action = QAction("Выключить и выйти", self)
        exit_action.triggered.connect(self.quit)
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        else:
            tray_btn.setEnabled(False)
            tray_btn.setToolTip("В этом окружении системный трей недоступен.")
        self.reward_title.textChanged.connect(self.update_reward_controls)
        self.cost.valueChanged.connect(self.update_reward_controls)
        self.amount.valueChanged.connect(self.update_reward_controls)
        for field in self.limit_fields.values():
            field.valueChanged.connect(self.update_reward_controls)
        self.tw_token.textChanged.connect(self.update_account_controls)
        self.ma_token.textChanged.connect(self.update_account_controls)
        worker.snapshot.connect(self.update_state)
        worker.notice.connect(self.notify)
        worker.open_url.connect(self.open_browser)
        worker.operation_finished.connect(self.finish_operation)
        worker.finished.connect(QApplication.instance().quit)
        self.shortcuts = []
        for index in range(3):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda page=index: self.change_page(page))
            self.shortcuts.append(shortcut)
            self.nav_buttons[index].setToolTip(f"Ctrl+{index + 1}")
        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self.focus_search)
        self.shortcuts.append(search_shortcut)
        self.change_page(0)
        self.update_reward_controls()
        self.update_account_controls()

    @staticmethod
    def scroll_page(content):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def collection_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        bridge, bridge_layout = surface("bridge")
        bridge_top = QHBoxLayout()
        self.reward_name = label("Награда ещё не создана", "section")
        bridge_top.addWidget(self.reward_name, 1)
        bridge_top.addWidget(button("Настроить", lambda: self.change_page(1), "quiet"))
        bridge_layout.addLayout(bridge_top)
        exchange = QHBoxLayout()
        points_box = QVBoxLayout()
        points_box.setSpacing(4)
        self.point_value = label("—", "exchange")
        points_box.addWidget(self.point_value)
        points_box.addWidget(label("баллов канала Twitch", "muted"))
        exchange.addLayout(points_box, 1)
        exchange.addWidget(label("→", "arrow"), 1, Qt.AlignmentFlag.AlignCenter)
        coins_box = QVBoxLayout()
        coins_box.setSpacing(4)
        self.coin_value = label("—", "coins")
        coins_box.addWidget(self.coin_value)
        coins_box.addWidget(label("мемкоинов на MemeAlerts", "muted"))
        exchange.addLayout(coins_box, 1)
        bridge_layout.addLayout(exchange)
        self.limit_summary = label("Подключите Twitch и MemeAlerts, затем создайте награду.", "muted", True)
        bridge_layout.addWidget(self.limit_summary)
        layout.addWidget(bridge)
        self.setup_frame, setup_layout = surface("setup")
        setup_layout.setContentsMargins(16, 12, 16, 12)
        setup_row = QHBoxLayout()
        self.setup_hint = label("Для начала подключите оба аккаунта.", wrap=True)
        setup_row.addWidget(self.setup_hint, 1)
        self.setup_action = button("Подключить аккаунты", self.open_next_step)
        setup_row.addWidget(self.setup_action)
        setup_layout.addLayout(setup_row)
        layout.addWidget(self.setup_frame)
        log_heading = QHBoxLayout()
        log_heading.addWidget(label("Журнал начислений", "section"))
        log_heading.addStretch()
        self.log_count = label("Последние 200 операций", "muted")
        log_heading.addWidget(self.log_count)
        layout.addLayout(log_heading)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти ник или текст операции")
        self.search.setAccessibleName("Поиск по журналу начислений")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render_journal)
        filters.addWidget(self.search, 1)
        self.log_filter = QComboBox()
        self.log_filter.setAccessibleName("Фильтр результата операции")
        for caption, value in (("Все операции", "all"), ("Начислено", "done"),
                               ("Баллы возвращены", "canceled"), ("Нужна проверка", "uncertain"),
                               ("В обработке", "pending")):
            self.log_filter.addItem(caption, value)
        self.log_filter.currentIndexChanged.connect(self.render_journal)
        filters.addWidget(self.log_filter)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 5)
        self.table.setMinimumHeight(160)
        self.table.setHorizontalHeaderLabels(["Время", "Ник MemeAlerts", "Мемкоины", "Результат", "Подробности"])
        self.table.setColumnHidden(4, True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.table, 1)
        self.empty = label("Получения награды появятся здесь. Новые заявки проверяются каждые 5 секунд.", "muted", True)
        layout.addWidget(self.empty)
        self.details_frame, details_layout = surface("details")
        details_layout.setContentsMargins(16, 13, 16, 13)
        self.detail_text = label(wrap=True)
        self.detail_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.detail_text)
        actions = QHBoxLayout()
        self.resolve_yes = button("Коины уже начислены", lambda: self.resolve(True))
        self.resolve_no = button("Вернуть баллы Twitch", lambda: self.resolve(False))
        actions.addWidget(self.resolve_yes)
        actions.addWidget(self.resolve_no)
        actions.addStretch()
        details_layout.addLayout(actions)
        self.details_frame.hide()
        self.resolve_yes.setEnabled(False)
        self.resolve_no.setEnabled(False)
        layout.addWidget(self.details_frame)
        return self.scroll_page(page)

    def reward_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        self.reward_panel, fields = surface()
        fields.addWidget(label("Что получает зритель", "section"))
        self.reward_title = QLineEdit("Мемкоины на MemeAlerts")
        self.reward_title.setMaxLength(45)
        fields.addWidget(form_row("Название награды", self.reward_title))
        exchange = QHBoxLayout()
        self.cost = SettingSpinBox()
        self.cost.setRange(1, 1_000_000_000)
        self.cost.setValue(1000)
        self.amount = SettingSpinBox()
        self.amount.setRange(1, 1_000_000)
        self.amount.setValue(10)
        exchange.addWidget(form_row("Цена в баллах Twitch", self.cost), 1)
        exchange.addWidget(form_row("Мемкоинов за получение", self.amount), 1)
        fields.addLayout(exchange)
        fields.addWidget(label("Зритель вводит ник MemeAlerts при получении награды. Если саппортер не найден, баллы возвращаются, а в чат приходит подсказка.", "muted", True))
        layout.addWidget(self.reward_panel)
        limits_panel, limits_layout = surface()
        limits_layout.addWidget(label("Защита от спама", "section"))
        limits_layout.addWidget(label("Лимиты применяет Twitch. Значение 0 отключает ограничение.", "muted", True))
        limit_form = QFormLayout()
        limit_form.setHorizontalSpacing(24)
        limit_form.setVerticalSpacing(14)
        limit_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.limit_fields = {}
        for key, title, tooltip in (
            ("cooldown_seconds", "Пауза между получениями, сек.", "Общая пауза для всех зрителей, максимум 7 дней. Паузы меньше 60 секунд не отображаются в интерфейсе Twitch, но действуют."),
            ("max_per_user_per_stream", "На одного зрителя за стрим", "Количество получений одним Twitch-зрителем за стрим."),
            ("max_per_stream", "На весь канал за стрим", "Количество получений всеми зрителями вместе за стрим."),
        ):
            field = SettingSpinBox()
            field.setRange(0, LIMIT_MAXIMA[key])
            field.setSpecialValueText("Без ограничения")
            field.setToolTip(tooltip)
            self.limit_fields[key] = field
            limit_form.addRow(title, field)
        limits_layout.addLayout(limit_form)
        layout.addWidget(limits_panel)
        self.reward_hint = label("Подключите аккаунты перед созданием награды.", "muted", True)
        layout.addWidget(self.reward_hint)
        save_row = QHBoxLayout()
        self.create_btn = button("Создать награду", self.save_reward, "primary")
        self.create_btn.setEnabled(False)
        save_row.addWidget(self.create_btn)
        self.reset_btn = button("Отменить изменения", self.reset_reward)
        self.reset_btn.setEnabled(False)
        save_row.addWidget(self.reset_btn)
        save_row.addStretch()
        layout.addLayout(save_row)
        layout.addStretch()
        return self.scroll_page(page)

    def connections_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 8, 0)
        self.accounts = QWidget()
        layout = QVBoxLayout(self.accounts)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        twitch, twitch_layout = surface()
        row = QHBoxLayout()
        row.addWidget(label("Twitch", "section"))
        row.addStretch()
        self.twitch_status = label("Не подключён", "muted")
        row.addWidget(self.twitch_status)
        twitch_layout.addLayout(row)
        twitch_layout.addWidget(label("Войдите аккаунтом владельца канала. Разрешите управление наградами и сообщения в чат.", "muted", True))
        self.device_btn = button("Войти в Twitch через браузер", lambda: self.send_command("device"))
        twitch_layout.addWidget(self.device_btn)
        self.manual_token = button("Ввести токен Twitch вручную", role="quiet")
        self.manual_token.setCheckable(True)
        twitch_layout.addWidget(self.manual_token)
        self.tw_token = QLineEdit()
        self.tw_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tw_token.setPlaceholderText("Токен Twitch с правами наград и чата")
        self.tw_token.setAccessibleName("Токен Twitch")
        self.tw_token.setClearButtonEnabled(True)
        self.tw_advanced = form_row("Токен Twitch", self.tw_token)
        self.tw_advanced.hide()
        self.manual_token.toggled.connect(self.tw_advanced.setVisible)
        twitch_layout.addWidget(self.tw_advanced)
        layout.addWidget(twitch)
        meme, meme_layout = surface()
        row = QHBoxLayout()
        row.addWidget(label("MemeAlerts", "section"))
        row.addStretch()
        self.meme_status = label("Не подключён", "muted")
        row.addWidget(self.meme_status)
        meme_layout.addLayout(row)
        meme_layout.addWidget(label("Вставьте accessToken аккаунта стримера. Он используется только для подключения к MemeAlerts.", "muted", True))
        token_row = QHBoxLayout()
        self.ma_token = QLineEdit()
        self.ma_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.ma_token.setPlaceholderText("accessToken с сайта MemeAlerts")
        self.ma_token.setAccessibleName("Токен MemeAlerts")
        self.ma_token.setClearButtonEnabled(True)
        token_row.addWidget(self.ma_token, 1)
        self.show_token = button("Показать")
        self.show_token.setCheckable(True)
        self.show_token.toggled.connect(self.toggle_token_visibility)
        token_row.addWidget(self.show_token)
        meme_layout.addLayout(token_row)
        meme_layout.addWidget(button("Где найти accessToken?", self.help, "quiet"))
        layout.addWidget(meme)
        self.remember = QCheckBox("Запомнить токены при подключении")
        self.remember.setToolTip("Токены сохраняются только в системном хранилище ключей, если оно доступно.")
        layout.addWidget(self.remember)
        layout.addWidget(label("Без сохранения токены действуют до выхода из приложения. После перезапуска сохранённые токены нужно подтвердить кнопкой подключения.", "muted", True))
        connect_row = QHBoxLayout()
        self.connect_btn = button("Подключить аккаунты", self.connect_accounts, "primary")
        connect_row.addWidget(self.connect_btn)
        connect_row.addStretch()
        layout.addLayout(connect_row)
        page_layout.addWidget(self.accounts)
        self.account_hint = label("", "muted", True)
        page_layout.addWidget(self.account_hint)
        page_layout.addStretch()
        return self.scroll_page(page)

    def change_page(self, page):
        self.pages.setCurrentIndex(page)
        self.nav_buttons[page].setChecked(True)
        titles = (("Сбор наград", "Баллы канала становятся мемкоинами."),
                  ("Настройка награды", "Цена, количество мемкоинов и ограничения для зрителей."),
                  ("Подключения", "Ваш Twitch-канал и аккаунт стримера на MemeAlerts."))
        self.page_title.setText(titles[page][0])
        self.page_subtitle.setText(titles[page][1])
        if page != 2:
            self.show_token.setChecked(False)

    def focus_search(self):
        self.change_page(0)
        self.search.setFocus()
        self.search.selectAll()

    def open_next_step(self):
        if not self.connected:
            self.change_page(2)
        elif not self.has_reward or self.is_dirty():
            self.change_page(1)
        else:
            self.log_filter.setCurrentIndex(self.log_filter.findData("uncertain"))
            self.render_journal()

    def toggle_token_visibility(self, checked):
        self.ma_token.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.show_token.setText("Скрыть" if checked else "Показать")

    def send_command(self, command, data=None):
        if self.busy or self.quitting:
            return
        self.warning = ""
        self.busy = True
        self.pending_command = command
        self.last_status = {"device": "Открываем вход в Twitch…", "connect": "Проверяем подключение аккаунтов…",
                            "create": "Создаём награду в Twitch…", "edit": "Сохраняем изменения награды…",
                            "start": "Обрабатываем очередь и включаем награду…", "stop": "Выключаем награду в Twitch…",
                            "resolve": "Сохраняем результат ручной проверки…"}.get(command, "Выполняем операцию…")
        self.render_status()
        self.update_reward_controls()
        self.update_account_controls()
        self.selection_changed()
        self.worker.submit(command, data)

    def finish_operation(self):
        self.busy = False
        self.pending_command = ""
        self.update_reward_controls()
        self.update_account_controls()
        self.selection_changed()

    def open_browser(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            self.notify("Не удалось открыть браузер. Откройте twitch.tv/activate и введите код из статуса приложения.")

    def connect_accounts(self):
        self.send_command("connect", {"twitch_token": self.tw_token.text(), "meme_token": self.ma_token.text(),
                                      "remember": self.remember.isChecked()})

    def toggle_collection(self):
        self.send_command("stop" if self.active else "start")

    def current_reward_settings(self):
        return {"title": self.reward_title.text(), "cost": self.cost.value(), "amount": self.amount.value(),
                "limits": {key: field.value() for key, field in self.limit_fields.items()}}

    def is_dirty(self):
        return bool(self.has_reward and self.current_reward_settings() != self.saved_settings)

    def save_reward(self):
        self.send_command("edit" if self.has_reward else "create", self.current_reward_settings())

    def reset_reward(self):
        if not self.saved_settings or self.active or self.busy:
            return
        self.load_reward_fields(self.saved_settings)
        self.update_reward_controls()

    def load_reward_fields(self, settings):
        self.reward_title.setText(settings["title"])
        self.cost.setValue(settings["cost"])
        self.amount.setValue(settings["amount"])
        for key, field in self.limit_fields.items():
            field.setValue(settings["limits"][key])

    def update_reward_controls(self):
        dirty = self.is_dirty()
        uncertain = any(row["state"] == "uncertain" for row in self.rows)
        ready = self.connected and self.has_reward and (self.active or (not dirty and not uncertain))
        self.toggle.setEnabled(ready and not self.busy)
        self.tray_toggle.setEnabled(ready and not self.busy)
        toggle_text = "Остановить сбор" if self.active else "Включить сбор"
        if self.busy and self.pending_command in ("start", "stop"):
            toggle_text = "Запускаем…" if self.pending_command == "start" else "Останавливаем…"
        self.toggle.setText(toggle_text)
        self.tray_toggle.setText("Остановить сбор" if self.active else "Включить сбор")
        reason = ("Сначала подключите оба аккаунта." if not self.connected else
                  "Сначала создайте награду." if not self.has_reward else
                  "Сначала сохраните или отмените изменения награды." if dirty else
                  "Сначала проверьте операции с неизвестным результатом." if uncertain else "")
        self.toggle.setToolTip(reason)
        saving = self.busy and self.pending_command in ("create", "edit")
        self.create_btn.setText("Сохраняем…" if saving else "Сохранить изменения" if self.has_reward else "Создать награду")
        self.create_btn.setEnabled(self.connected and not self.active and not self.busy and (not self.has_reward or dirty))
        self.reset_btn.setEnabled(dirty and not self.active and not self.busy)
        self.nav_buttons[1].setText("Награда *" if dirty else "Награда")
        for field in (self.reward_title, self.cost, self.amount, *self.limit_fields.values()):
            field.setEnabled(not self.active and not self.busy)
        if self.active:
            hint = "Чтобы изменить награду, сначала остановите сбор."
        elif not self.connected:
            hint = "Подключите оба аккаунта на экране «Подключения»."
        elif dirty:
            hint = "Есть несохранённые изменения. Старые заявки сохранят прежнее количество мемкоинов."
        elif self.has_reward:
            hint = "Награда сохранена. Можно включить сбор."
        else:
            hint = "Награда создаётся выключенной. После создания включите сбор."
        self.reward_hint.setText(hint)
        self.update_summary()

    def update_account_controls(self):
        self.accounts.setEnabled(not self.active and not self.busy)
        self.connect_btn.setEnabled(bool(self.tw_token.text().strip() and self.ma_token.text().strip())
                                    and not self.active and not self.busy)
        self.connect_btn.setText("Проверяем…" if self.busy and self.pending_command == "connect" else
                                 "Переподключить аккаунты" if self.connected else "Подключить аккаунты")
        self.account_hint.setText("Для смены аккаунтов сначала остановите сбор." if self.active else "")
        if not self.connected:
            self.twitch_status.setText("Токен получен" if self.tw_token.text() else "Не подключён")
            self.meme_status.setText("Токен введён" if self.ma_token.text() else "Не подключён")

    def update_summary(self):
        settings = self.saved_settings if self.has_reward else {}
        self.reward_name.setText(settings.get("title", "Создайте первую награду"))
        self.point_value.setText(f'{settings["cost"]:,}'.replace(",", " ") if settings else "—")
        self.coin_value.setText(f'{settings["amount"]:,}'.replace(",", " ") if settings else "—")
        if settings:
            limits = settings["limits"]
            parts = []
            if limits["cooldown_seconds"]:
                parts.append(f'Пауза {limits["cooldown_seconds"]} сек.')
            if limits["max_per_user_per_stream"]:
                parts.append(f'На зрителя за стрим: {limits["max_per_user_per_stream"]}')
            if limits["max_per_stream"]:
                parts.append(f'На канал за стрим: {limits["max_per_stream"]}')
            self.limit_summary.setText("   /   ".join(parts) if parts else "Ограничения получений не установлены.")
        else:
            self.limit_summary.setText("Зритель вводит ник на MemeAlerts и получает мемкоины за баллы канала.")
        uncertain = sum(row["state"] == "uncertain" for row in self.rows)
        show_setup = not self.connected or not self.has_reward or self.is_dirty() or bool(uncertain)
        self.setup_frame.setVisible(show_setup)
        if not self.connected:
            text, action = "Подключите Twitch и MemeAlerts, чтобы начать работу.", "Подключить аккаунты"
        elif not self.has_reward:
            text, action = "Аккаунты подключены. Создайте награду для зрителей.", "Создать награду"
        elif self.is_dirty():
            text, action = "Сохраните или отмените изменения перед запуском сбора.", "Открыть настройки"
        else:
            text, action = f"Операций для ручной проверки: {uncertain}. Проверьте их перед запуском.", "Проверить операции"
        self.setup_hint.setText(text)
        self.setup_action.setText(action)

    def update_state(self, state):
        for key, field in (("twitch_token", self.tw_token), ("meme_token", self.ma_token)):
            if key in state:
                field.setText(state[key])
        if "remember" in state:
            self.remember.setChecked(state["remember"])
        if "active" not in state:
            return
        previous_connected = self.connected
        self.active, self.connected = state["active"], state["connected"]
        self.has_reward = bool(state["reward_id"])
        self.badge.setText("Сбор включён" if self.active else "Сбор выключен")
        self.badge.setProperty("active", self.active)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        settings = state["settings"]
        if settings:
            settings = dict(settings, limits=normalize_limits(settings.get("limits")))
        if settings and (self.loaded_reward != state["reward_id"] or settings != self.saved_settings):
            self.loaded_reward = state["reward_id"]
            self.saved_settings = settings
            self.load_reward_fields(settings)
        self.rows = state["rows"]
        self.update_reward_controls()
        self.update_account_controls()
        self.last_status = state["status"]
        self.render_status()
        names = state.get("identities", {})
        self.connection_indicator.setText("Аккаунты подключены" if self.connected else "Аккаунты\nне подключены")
        if self.connected:
            self.twitch_status.setText(names.get("twitch") or "Подключён")
            self.meme_status.setText(names.get("memealerts") or "Подключён")
        if self.connected and not previous_connected:
            self.show_token.setChecked(False)
            self.manual_token.setChecked(False)
            self.change_page(0 if self.has_reward else 1)
        self.tray.setToolTip("MemeAlerts Award — " + self.badge.text())
        self.tray.setIcon(icon(self.active))
        self.render_journal()

    def render_journal(self):
        selected = self.selected_id()
        scroll = self.table.verticalScrollBar().value()
        query = self.search.text().strip().casefold()
        state = self.log_filter.currentData()
        rows = []
        for row in self.rows:
            if state == "pending" and row["state"] not in ("pending", "sending", "credited", "cancel_pending"):
                continue
            if state not in ("all", "pending") and row["state"] != state:
                continue
            haystack = f'{row["nickname"]} {row["note"]} {STATES.get(row["state"], "")}'.casefold()
            if query and query not in haystack:
                continue
            rows.append(row)
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            timestamp = datetime.fromisoformat(row["updated"]).astimezone().strftime("%d.%m  %H:%M:%S")
            values = [timestamp, row["nickname"], str(row["amount"]), STATES.get(row["state"], row["state"]), row["note"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(row["note"] if column == 3 else value)
                item.setData(Qt.ItemDataRole.UserRole, (row["id"], row["state"]))
                if column == 3:
                    color = "#78d5b0" if row["state"] == "done" else "#efbf73" if row["state"] == "uncertain" else "#b1bac6"
                    item.setForeground(QColor(color))
                self.table.setItem(index, column, item)
            if selected == row["id"]:
                self.table.selectRow(index)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        self.log_count.setText(f"Показано: {len(rows)} из {len(self.rows)}")
        self.empty.setVisible(not rows)
        self.empty.setText("По этому запросу операций нет. Измените поиск или фильтр." if self.rows else
                           "Пока нет операций. Когда зритель получит награду, здесь появится результат. Проверяем очередь каждые 5 секунд.")
        self.selection_changed()

    def selected_id(self):
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole)[0] if items else None

    def selection_changed(self):
        rid = self.selected_id()
        row = next((row for row in self.rows if row["id"] == rid), None)
        self.details_frame.setVisible(row is not None)
        uncertain = bool(row and row["state"] == "uncertain")
        self.resolve_yes.setVisible(uncertain)
        self.resolve_no.setVisible(uncertain)
        enabled = uncertain and not self.active and not self.busy
        self.resolve_yes.setEnabled(enabled)
        self.resolve_no.setEnabled(enabled)
        if row:
            self.detail_text.setText(f'{row["nickname"]} — {STATES.get(row["state"], row["state"])}\n{row["note"] or "Подробности появятся после обработки."}')

    def resolve(self, credited):
        rid = self.selected_id()
        if not rid:
            return
        message = ("Вы проверили баланс на MemeAlerts и убедились, что коины начислены? Награда Twitch будет выполнена."
                   if credited else "Вы проверили баланс и убедились, что коины НЕ начислены? Баллы Twitch будут возвращены зрителю.")
        if QMessageBox.question(self, "Ручная проверка начисления", message) == QMessageBox.StandardButton.Yes:
            self.send_command("resolve", (rid, credited))

    def help(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Подключение MemeAlerts")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("1. Войдите на memealerts.com аккаунтом стримера.\n\n"
                       "2. Откройте инструменты разработчика браузера (F12), затем Application → Local Storage → https://memealerts.com. "
                       "В Firefox этот раздел называется «Хранилище» → «Локальное хранилище».\n\n"
                       "3. Скопируйте значение accessToken и вставьте в поле MemeAlerts.\n\n"
                       "Токен даёт доступ к вашему аккаунту. Не отправляйте его другим людям.")
        open_site = dialog.addButton("Открыть MemeAlerts", QMessageBox.ButtonRole.ActionRole)
        open_site.clicked.connect(lambda: self.open_browser("https://memealerts.com/"))
        dialog.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

    def render_status(self):
        self.status.setText(self.warning or self.last_status)
        self.notice_frame.setProperty("warning", bool(self.warning))
        self.notice_frame.style().unpolish(self.notice_frame)
        self.notice_frame.style().polish(self.notice_frame)
        self.dismiss_notice.setVisible(bool(self.warning))

    def clear_warning(self):
        if self.last_status == self.warning:
            self.last_status = "Сбор включён." if self.active else "Сбор выключен."
        self.warning = ""
        self.render_status()

    def notify(self, message):
        self.warning = message
        self.render_status()
        self.tray.showMessage("MemeAlerts Award", message, QSystemTrayIcon.MessageIcon.Warning)

    def minimize_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
        elif QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.setEnabled(False)
        self.last_status = "Выключаем награду и завершаем текущую операцию…"
        self.warning = ""
        self.render_status()
        self.worker.submit("exit")


STYLE = """
QWidget { background: #202326; color: #eef1f5; font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 13px; }
QWidget:disabled { color: #89939d; }
QWidget#formField { background: transparent; }
QLabel { background: transparent; }
QLabel#brand { color: #90c5ff; font-size: 23px; font-weight: 750; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#section { font-size: 16px; font-weight: 650; }
QLabel#muted { color: #aab4be; font-size: 12px; }
QLabel#fieldLabel { color: #cbd3dc; font-weight: 600; }
QLabel#badge { border: 1px solid #4b535c; color: #c4cdd6; padding: 7px 11px; border-radius: 12px; font-size: 12px; }
QLabel#badge[active=true] { color: #78d5b0; border-color: #3c6e5c; background: #233b31; }
QLabel#exchange, QLabel#coins { font-size: 33px; font-weight: 700; }
QLabel#coins { color: #edc17d; }
QLabel#arrow { font-size: 30px; color: #8eabc8; }
QFrame#sidebar { background: #191c1f; border-right: 1px solid #353b42; }
QFrame#surface, QFrame#bridge { background: #292e33; border: 1px solid #424a53; border-radius: 10px; }
QFrame#bridge { border-left: 3px solid #75b8ff; }
QFrame#setup { background: #273541; border: 1px solid #45617a; border-radius: 7px; }
QFrame#details { background: #2a3036; border-left: 2px solid #75a9d9; }
QFrame#notice { background: #292e33; border: 1px solid #424a53; border-radius: 6px; }
QFrame#notice[warning=true] { background: #3b3026; border-color: #947348; }
QWidget QScrollArea { border: 0; }
QLineEdit, QSpinBox, QComboBox { background: #20252a; border: 1px solid #576370; border-radius: 5px; padding: 9px 10px; min-height: 18px; selection-background-color: #345e82; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #8dc5ff; }
QLineEdit:disabled, QSpinBox:disabled { background: #2b3035; border-color: #434b54; }
QSpinBox::up-button, QSpinBox::down-button { width: 21px; }
QComboBox { min-width: 145px; padding-right: 23px; }
QComboBox QAbstractItemView { background: #292e33; color: #eef1f5; selection-background-color: #345574; }
QPushButton { background: #35424e; border: 1px solid #596a78; padding: 9px 13px; border-radius: 5px; font-weight: 600; min-height: 18px; }
QPushButton:hover { background: #425363; border-color: #92a9bd; }
QPushButton:focus { border-color: #c3e1ff; }
QPushButton:pressed { background: #355775; }
QPushButton:disabled { background: #2c3238; border-color: #46515c; color: #929eaa; }
QPushButton#primary { background: #86bfff; border-color: #a3d0ff; color: #152435; }
QPushButton#primary:hover { background: #a4d0ff; }
QPushButton#primary:disabled { background: #35424e; color: #9aa8b4; border-color: #4e5e6d; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #acbdd0; padding-left: 3px; padding-right: 3px; }
QPushButton#quiet:hover { color: #dbecff; background: #303c47; }
QPushButton#nav { background: transparent; text-align: left; border-color: transparent; padding: 12px 10px; color: #acb7c2; }
QPushButton#nav:hover { background: #29333c; }
QPushButton#nav:checked { background: #293e51; color: #dceeff; border-left: 3px solid #88c2ff; }
QCheckBox { background: transparent; spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #8b9baa; border-radius: 3px; background: #21272d; }
QCheckBox::indicator:checked { background: #86bfff; border: 2px solid #d5eaff; }
QTableWidget { background: #252a2f; alternate-background-color: #2b3137; border: 1px solid #454f59; border-radius: 7px; }
QHeaderView::section { background: #343e48; color: #c7d2dc; border: 0; padding: 10px; font-size: 12px; }
QTableWidget::item { padding: 8px; }
QTableWidget::item:selected { background: #324d65; color: #f0f7ff; }
QScrollBar:vertical { background: #202326; width: 9px; }
QScrollBar::handle:vertical { background: #526371; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #292e33; border: 1px solid #596a78; padding: 6px; }
QMenu::item { padding: 9px 18px; }
QMenu::item:selected { background: #345574; }
QToolTip { background: #34424d; color: #f0f5fa; border: 1px solid #71899d; padding: 6px; }
"""
