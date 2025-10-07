pyside6_simple_dark_theme = """
    QWidget {
        background-color: #2b2b2b;
        color: #dddddd;
        font-family: Segoe UI, sans-serif;
        font-size: 10pt;
    }

    QWidget:disabled {
        color: #777777;
        background-color: #2a2a2a;
    }

    QLabel, QCheckBox, QRadioButton, QGroupBox, QMenu, QTabBar::tab {
        background: transparent;
    }

    QPushButton {
        background-color: #3c3f41;
        border: 1px solid #5c5c5c;
        border-radius: 4px;
        padding: 6px 12px;
        color: #ffffff;
    }

    QPushButton:hover {
        background-color: #505354;
        border: 1px solid #6c6c6c;
    }

    QPushButton:pressed {
        background-color: #2b2b2b;
        border: 1px solid #787878;
    }

    QPushButton:disabled {
        background-color: #2f2f2f;
        border: 1px solid #444444;
        color: #777777;
    }

    QCheckBox {
        spacing: 5px;
        color: #dddddd;
    }

    QCheckBox:disabled {
        color: #777777;
    }

    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid #aaaaaa;
        border-radius: 3px;
        background: #2b2b2b;
    }

    QCheckBox::indicator:checked {
        background-color: #007acc;
        border: 1px solid #007acc;
    }

    QCheckBox::indicator:unchecked:hover {
        border: 1px solid #ffffff;
    }

    QRadioButton {
        spacing: 5px;
        color: #dddddd;
    }

    QRadioButton:disabled {
        color: #777777;
    }

    QRadioButton::indicator {
        width: 16px;
        height: 16px;
        border: 1px solid #aaaaaa;
        border-radius: 8px;
        background: #2b2b2b;
    }

    QRadioButton::indicator:checked {
        background-color: #007acc;
        border: 1px solid #007acc;
    }

    QRadioButton::indicator:unchecked:hover {
        border: 1px solid #ffffff;
    }

    QLineEdit, QTextEdit {
        background-color: #3c3f41;
        border: 1px solid #5c5c5c;
        color: #ffffff;
        border-radius: 4px;
        padding: 5px;
    }

    QLineEdit:disabled, QTextEdit:disabled {
        background-color: #2f2f2f;
        color: #777777;
        border: 1px solid #444444;
    }

    QProgressBar {
        border: 1px solid #3a3a3a;
        border-radius: 5px;
        background-color: #2e2e2e;
        text-align: center;
        color: #ffffff;
    }

    QProgressBar::chunk {
        background-color: #00c853;
        width: 10px;
        margin: 0.5px;
    }

    QProgressBar:disabled {
        color: #777777;
        background-color: #2a2a2a;
    }

    QScrollBar:vertical, QScrollBar:horizontal {
        background-color: #2b2b2b;
        border: none;
        margin: 16px 0 16px 0;
    }

    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background-color: #5c5c5c;
        min-height: 20px;
        border-radius: 4px;
    }

    QScrollBar:disabled {
        background: #2a2a2a;
    }

    QTabWidget::pane {
        border-top: 2px solid #3c3f41;
    }

    QTabBar::tab {
        background: transparent;
        color: #b1b1b1;
        padding: 8px 16px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        margin-right: 2px;
    }

    QTabBar::tab:selected {
        background-color: #3c3f41;
        color: #ffffff;
    }

    QTabBar::tab:hover {
        background-color: #444444;
    }

    QTabBar::tab:disabled {
        background-color: #2f2f2f;
        color: #666666;
    }

    QListWidget, QComboBox, QListView {
        background-color: #3c3f41;
        color: #dddddd;
        selection-background-color: #007acc;
        selection-color: #ffffff;
        border: 1px solid #5c5c5c;
        border-radius: 4px;
        padding: 5px;
    }

    QListWidget::item:selected,
    QComboBox QAbstractItemView::item:selected {
        background-color: #007acc;
        color: #ffffff;
    }

    QListWidget:disabled, QComboBox:disabled, QListView:disabled {
        background-color: #2f2f2f;
        color: #777777;
        border: 1px solid #444444;
    }

    QMenuBar {
        background-color: #2b2b2b;  
        color: #dddddd;
        spacing: 3px;
        padding: 4px;
    }

    QMenuBar::item {
        background-color: #2b2b2b; 
        color: #dddddd;
        padding: 4px 10px;
    }

    QMenuBar::item:selected {
        background-color: #505354;
        color: #ffffff;
    }

    QMenuBar::item:pressed {
        background-color: #3c3f41;
    }

    QMenuBar:disabled {
        color: #777777;
    }

    QMenu {
        background: #2b2b2b;
        color: #dddddd;
        border: 1px solid #5c5c5c;
    }

    QMenu::item {
        padding: 6px 24px;
        background-color: transparent;
        color: #dddddd;
    }

    QMenu::item:selected {
        background-color: #505354;
        color: #ffffff;
    }

    QMenu::separator {
        height: 1px;
        background: #5c5c5c;
        margin: 5px 10px;
    }

    QMenu:disabled {
        color: #777777;
        background-color: transparent;
    }
"""
