"""Stable QMainWindow shell with four-point preprocessing controls."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QSettings, QSize, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from logdigitizer import __version__
from logdigitizer.application.recent_projects import RecentProjectsStore
from logdigitizer.application.project_service import (
    ImageOverlapRelation,
    ProjectService,
)
from logdigitizer.domain.calibration import (
    CalibrationError,
    DEFAULT_HORIZONTAL_SCALE_ID,
    DepthCalibration,
    DepthUnit,
)
from logdigitizer.gui.calibration_panel import (
    CalibrationPanel,
    DepthValueDialog,
)
from logdigitizer.gui.curve_calibration_controller import (
    CurveCalibrationController,
)
from logdigitizer.gui.curve_join_overlay import (
    ImageOverlapGuideDefinition,
)
from logdigitizer.gui.automatic_digitization_controller import (
    AutomaticDigitizationController,
)
from logdigitizer.gui.automatic_digitization_panel import (
    AutomaticDigitizationPanel,
)
from logdigitizer.gui.icons import app_icon
from logdigitizer.gui.las_export_dialog import LasExportDialog
from logdigitizer.gui.las_curve_preview import LasCurvePreviewDialog
from logdigitizer.gui.manual_digitization_controller import (
    ManualDigitizationController,
)
from logdigitizer.gui.live_enhancement import LiveEnhancementController
from logdigitizer.gui.preprocess_panel import PreprocessPanel
from logdigitizer.gui.quality_control_dialog import QualityControlDialog
from logdigitizer.gui.project_panel import ProjectPanel
from logdigitizer.gui.rectification_preview import (
    RectificationPreviewDialog,
)
from logdigitizer.gui.viewer import ImageViewer
from logdigitizer.io.image_io import SUPPORTED_IMAGE_SUFFIXES
from logdigitizer.processing.calibration import (
    CalibrationRangeError,
    depth_to_pixel_y,
    pixel_y_to_depth,
)
from logdigitizer.processing.geometry import (
    InvalidQuadrilateralError,
)
from logdigitizer.utils.logging import get_logger
from logdigitizer.utils.paths import (
    safe_project_stem,
    user_guide_path,
)
from logdigitizer.utils.validation import LogDigitizerError

logger = get_logger("main_window")


class NewProjectDialog(QDialog):
    """Minimal dialog for creating the canonical project directory."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Project")
        self.name_edit = QLineEdit()
        self.well_edit = QLineEdit()
        self.directory_edit = QLineEdit()
        self.description_edit = QPlainTextEdit()
        self.description_edit.setMaximumHeight(90)

        browse = QPushButton("Browse…")
        browse.clicked.connect(
            self._browse_directory
        )

        directory_row = QHBoxLayout()
        directory_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        directory_row.addWidget(
            self.directory_edit,
            1,
        )
        directory_row.addWidget(browse)

        directory_widget = QWidget()
        directory_widget.setLayout(
            directory_row
        )

        form = QFormLayout()
        form.addRow(
            "Project name*",
            self.name_edit,
        )
        form.addRow(
            "Well / borehole name",
            self.well_edit,
        )
        form.addRow(
            "Project directory*",
            directory_widget,
        )
        form.addRow(
            "Description",
            self.description_edit,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(
            self._validate_and_accept
        )
        buttons.rejected.connect(
            self.reject
        )

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(560, 300)

    @property
    def values(self) -> dict[str, str]:
        return {
            "name": self.name_edit.text().strip(),
            "well_name": self.well_edit.text().strip(),
            "project_directory": (
                self.directory_edit.text().strip()
            ),
            "description": (
                self.description_edit
                .toPlainText()
                .strip()
            ),
        }

    def _browse_directory(self) -> None:
        parent_dir = QFileDialog.getExistingDirectory(
            self,
            "Choose parent directory",
        )
        if not parent_dir:
            return

        name = self.name_edit.text().strip()
        folder = (
            f"{safe_project_stem(name)}_project"
            if name
            else "log_project"
        )
        self.directory_edit.setText(
            str(Path(parent_dir) / folder)
        )

    def _validate_and_accept(self) -> None:
        values = self.values

        if not values["name"]:
            QMessageBox.warning(
                self,
                "New Project",
                "Project name cannot be empty.",
            )
            return

        if not values["project_directory"]:
            QMessageBox.warning(
                self,
                "New Project",
                "Choose a project directory.",
            )
            return

        self.accept()


class MainWindow(QMainWindow):
    """Main presentation shell; scientific/domain logic lives outside widgets."""

    def __init__(
        self,
        service: ProjectService | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()

        self.service = (
            service or ProjectService()
        )
        self.settings = settings or QSettings(
            "IGME-CSIC",
            "LogDigitizer",
        )
        self.recent_projects = RecentProjectsStore(self.settings)
        self.viewer = ImageViewer()
        self.live_enhancement = LiveEnhancementController(self.viewer, self)
        self.project_panel = ProjectPanel()
        self.preprocess_panel = PreprocessPanel()
        self.calibration_panel = CalibrationPanel()
        self.digitization_panel = AutomaticDigitizationPanel()
        self._quality_control_dialog: QualityControlDialog | None = None
        self._las_curve_preview_dialog: LasCurvePreviewDialog | None = None
        self._active_depth_calibration: DepthCalibration | None = None
        # Tracks which right-hand workspace most recently defined the HUD's
        # working scale context. Scientific project data are never changed.
        self._hud_scale_context_source = "calibration"
        self._welcome_recent_buttons: list[QPushButton] = []

        self.welcome_page = self._build_welcome_page()
        self.central_stack = QStackedWidget()
        self.central_stack.addWidget(self.welcome_page)
        self.central_stack.addWidget(self.viewer)
        self.setCentralWidget(self.central_stack)
        self.setWindowTitle(
            "LogDigitizer"
        )
        self.resize(1350, 850)

        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self.curve_calibration_controller = CurveCalibrationController(
            self.service,
            self.viewer,
            self.calibration_panel,
            self.curve_value_status,
            error_callback=self._show_error,
            title_refresh_callback=self._refresh_project_state,
            message_callback=self.statusBar().showMessage,
            parent=self,
        )
        self.manual_digitization_controller = ManualDigitizationController(
            self.service,
            self.viewer,
            self.digitization_panel,
            prepare_capture_callback=self._prepare_manual_digitization_capture,
            title_refresh_callback=self._refresh_project_state,
            message_callback=self.statusBar().showMessage,
            error_callback=self._show_error,
            parent=self,
        )
        self.automatic_digitization_controller = AutomaticDigitizationController(
            self.service,
            self.viewer,
            self.digitization_panel,
            prepare_capture_callback=self._prepare_manual_digitization_capture,
            title_refresh_callback=self._refresh_project_state,
            digitization_refresh_callback=(
                lambda curve_id: self.manual_digitization_controller.refresh(
                    selected_curve_id=curve_id
                )
            ),
            record_edit_callback=(
                self.manual_digitization_controller._record_applied_edit
            ),
            message_callback=self.statusBar().showMessage,
            error_callback=self._show_error,
            parent=self,
        )
        self.manual_digitization_controller.undo_stack.indexChanged.connect(
            self.automatic_digitization_controller.refresh_persisted_raw_overlay
        )
        self._connect_signals()
        self._refresh_all()

    def _build_welcome_page(self) -> QWidget:
        """Create the no-project start page with the shared recent history."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(80, 55, 80, 55)
        outer.addStretch(1)

        content = QWidget()
        content.setMaximumWidth(760)
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        title = QLabel("LogDigitizer")
        title.setStyleSheet("font-size: 30px; font-weight: 700;")
        subtitle = QLabel(
            "Open an existing project or continue with one of your recent projects."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #6B7280; font-size: 13px;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        action_row = QHBoxLayout()
        self.welcome_open_button = QPushButton(
            app_icon("folder-open"),
            "Open Project…",
        )
        self.welcome_new_button = QPushButton(
            app_icon("file"),
            "New Project",
        )
        self.welcome_open_button.clicked.connect(self.open_project)
        self.welcome_new_button.clicked.connect(self.new_project)
        action_row.addWidget(self.welcome_open_button)
        action_row.addWidget(self.welcome_new_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        recent_title = QLabel("Recent Projects")
        recent_title.setStyleSheet(
            "font-size: 16px; font-weight: 600; margin-top: 14px;"
        )
        layout.addWidget(recent_title)

        self.welcome_recent_layout = QVBoxLayout()
        self.welcome_recent_layout.setSpacing(7)
        layout.addLayout(self.welcome_recent_layout)

        horizontal = QHBoxLayout()
        horizontal.addStretch(1)
        horizontal.addWidget(content, 1)
        horizontal.addStretch(1)
        outer.addLayout(horizontal)
        outer.addStretch(2)
        return page

    def _build_docks(self) -> None:
        project_dock = QDockWidget(
            "Project Explorer",
            self,
        )
        project_dock.setObjectName(
            "projectDock"
        )
        project_dock.setWidget(
            self.project_panel
        )
        self.addDockWidget(
            Qt.DockWidgetArea.LeftDockWidgetArea,
            project_dock,
        )

        self.preprocessing_dock = QDockWidget(
            "Preprocessing",
            self,
        )
        self.preprocessing_dock.setObjectName(
            "propertiesDock"
        )
        self.preprocessing_dock.setWidget(
            self.preprocess_panel
        )
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.preprocessing_dock,
        )

        self.calibration_dock = QDockWidget(
            "Calibration",
            self,
        )
        self.calibration_dock.setObjectName(
            "calibrationDock"
        )
        self.calibration_dock.setWidget(
            self.calibration_panel
        )
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.calibration_dock,
        )
        self.digitization_dock = QDockWidget(
            "Digitization",
            self,
        )
        self.digitization_dock.setObjectName(
            "digitizationDock"
        )
        self.digitization_dock.setWidget(
            self.digitization_panel
        )
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.digitization_dock,
        )

        self.tabifyDockWidget(
            self.preprocessing_dock,
            self.calibration_dock,
        )
        self.tabifyDockWidget(
            self.calibration_dock,
            self.digitization_dock,
        )
        self.preprocessing_dock.raise_()

    def _build_actions(self) -> None:
        self.new_action = QAction(
            "New Project",
            self,
        )
        self.new_action.setShortcut(
            QKeySequence.StandardKey.New
        )

        self.open_action = QAction(
            "Open Project",
            self,
        )
        self.open_action.setShortcut(
            QKeySequence.StandardKey.Open
        )

        self.save_action = QAction(
            "Save Project",
            self,
        )
        self.save_action.setShortcut(
            QKeySequence.StandardKey.Save
        )

        self.add_action = QAction(
            "Add Image",
            self,
        )
        self.quality_control_action = QAction(
            "Quality Control",
            self,
        )
        self.las_curve_preview_action = QAction(
            "LAS Curve Preview",
            self,
        )
        self.export_las_action = QAction(
            "Export LAS…",
            self,
        )
        self.exit_action = QAction(
            "Exit",
            self,
        )
        self.exit_action.setShortcut(
            QKeySequence.StandardKey.Quit
        )

        self.zoom_in_action = QAction(
            "Zoom In",
            self,
        )
        self.zoom_in_action.setShortcut(
            QKeySequence.StandardKey.ZoomIn
        )
        self.zoom_out_action = QAction(
            "Zoom Out",
            self,
        )
        self.zoom_out_action.setShortcut(
            QKeySequence.StandardKey.ZoomOut
        )
        self.fit_action = QAction(
            "Fit to Window",
            self,
        )
        self.fit_action.setShortcut("F")

        self.actual_action = QAction(
            "Actual Size / 1:1",
            self,
        )
        self.actual_action.setShortcut("1")

        self.pan_action = QAction(
            "Pan",
            self,
        )
        self.pan_action.setCheckable(True)
        self.pan_action.setChecked(True)

        self.user_guide_action = QAction(
            "User Guide",
            self,
        )

        self.about_action = QAction(
            "About",
            self,
        )

        # Font Awesome 6 Solid presentation. Action text is retained for menus,
        # keyboard navigation and accessibility; only the project-level LAS
        # export keeps its text visible in the otherwise icon-only toolbar.
        self.new_action.setIcon(app_icon("file"))
        self.open_action.setIcon(app_icon("folder-open"))
        self.save_action.setIcon(app_icon("floppy-disk"))
        self.add_action.setIcon(app_icon("image"))
        self.quality_control_action.setIcon(app_icon("clipboard-check"))
        self.las_curve_preview_action.setIcon(app_icon("chart-line"))
        self.export_las_action.setIcon(app_icon("file-export"))
        self.zoom_in_action.setIcon(app_icon("magnifying-glass-plus"))
        self.zoom_out_action.setIcon(app_icon("magnifying-glass-minus"))
        self.fit_action.setIcon(app_icon("expand"))
        self.pan_action.setIcon(app_icon("hand"))
        self.user_guide_action.setIcon(app_icon("file-pdf"))

        self.new_action.setToolTip("New Project")
        self.open_action.setToolTip("Open Project")
        self.save_action.setToolTip("Save Project")
        self.quality_control_action.setToolTip(
            "Review digitization continuity and LAS sampling gaps"
        )
        self.las_curve_preview_action.setToolTip(
            "Preview the final reconstructed curves prepared for LAS export"
        )
        self.export_las_action.setToolTip("Export project curves to LAS 2.0")
        self.fit_action.setToolTip("Fit to Window")

        self.new_action.triggered.connect(
            self.new_project
        )
        self.open_action.triggered.connect(
            self.open_project
        )
        self.save_action.triggered.connect(
            self.save_project
        )
        self.add_action.triggered.connect(
            self.add_images
        )
        self.quality_control_action.triggered.connect(
            lambda: self.open_quality_control()
        )
        self.las_curve_preview_action.triggered.connect(
            self.open_las_curve_preview
        )
        self.export_las_action.triggered.connect(
            self.export_las
        )
        self.exit_action.triggered.connect(
            self.close
        )
        self.zoom_in_action.triggered.connect(
            self.viewer.zoom_in
        )
        self.zoom_out_action.triggered.connect(
            self.viewer.zoom_out
        )
        self.fit_action.triggered.connect(
            self.viewer.fit_to_window
        )
        self.actual_action.triggered.connect(
            self.viewer.actual_size
        )
        self.pan_action.toggled.connect(
            self.viewer.set_pan_enabled
        )
        self.user_guide_action.triggered.connect(
            self.open_user_guide
        )
        self.about_action.triggered.connect(
            self.show_about
        )

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(
            "File"
        )
        file_menu.addAction(
            self.new_action
        )
        file_menu.addAction(
            self.open_action
        )
        self.recent_projects_menu = file_menu.addMenu(
            "Recent Projects"
        )
        self.recent_projects_menu.setIcon(app_icon("folder"))
        file_menu.addAction(
            self.save_action
        )
        file_menu.addSeparator()
        file_menu.addAction(
            self.add_action
        )
        file_menu.addAction(
            self.quality_control_action
        )
        file_menu.addAction(
            self.las_curve_preview_action
        )
        file_menu.addAction(
            self.export_las_action
        )
        file_menu.addSeparator()
        file_menu.addAction(
            self.exit_action
        )

        view_menu = self.menuBar().addMenu(
            "View"
        )
        view_menu.addAction(
            self.zoom_in_action
        )
        view_menu.addAction(
            self.zoom_out_action
        )
        view_menu.addAction(
            self.fit_action
        )
        view_menu.addAction(
            self.actual_action
        )
        view_menu.addAction(
            self.pan_action
        )

        help_menu = self.menuBar().addMenu(
            "Help"
        )
        help_menu.addAction(
            self.user_guide_action
        )
        help_menu.addSeparator()
        help_menu.addAction(
            self.about_action
        )

    def _build_toolbar(self) -> None:
        toolbar = QToolBar(
            "Main",
            self,
        )
        toolbar.setObjectName(
            "mainToolbar"
        )
        toolbar.setMovable(True)
        toolbar.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        toolbar.setIconSize(QSize(20, 20))

        # Add Image remains available from File / the project panel, and 1:1
        # remains available from View / shortcut "1"; both are intentionally
        # omitted from the compact main toolbar. Quality Control, LAS Curve
        # Preview and Export LAS keep visible labels because they are
        # project-level workflow actions.
        for action in (
            self.new_action,
            self.open_action,
            self.save_action,
            self.quality_control_action,
            self.las_curve_preview_action,
            self.export_las_action,
        ):
            toolbar.addAction(action)

        for labelled_action in (
            self.quality_control_action,
            self.las_curve_preview_action,
            self.export_las_action,
        ):
            labelled_button = toolbar.widgetForAction(labelled_action)
            if labelled_button is not None:
                labelled_button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                )

        toolbar.addSeparator()

        for action in (
            self.zoom_in_action,
            self.zoom_out_action,
            self.fit_action,
            self.pan_action,
        ):
            toolbar.addAction(action)

        # The specification requests no tooltip for these conventional
        # navigation actions.
        for action in (
            self.zoom_in_action,
            self.zoom_out_action,
            self.pan_action,
        ):
            button = toolbar.widgetForAction(action)
            if button is not None:
                button.setToolTip("")

        self.main_toolbar = toolbar
        self.addToolBar(toolbar)

    def _build_status_bar(self) -> None:
        self.segment_status = QLabel(
            "Segment: —"
        )
        self.x_status = QLabel(
            "X: —"
        )
        self.y_status = QLabel(
            "Y: —"
        )
        self.depth_status = QLabel(
            "Depth: Not calibrated"
        )
        self.curve_value_status = QLabel(
            "Curve: No curve selected"
        )
        self.zoom_status = QLabel(
            "Zoom: —"
        )

        self.statusBar().addPermanentWidget(
            self.segment_status
        )
        self.statusBar().addPermanentWidget(
            self.x_status
        )
        self.statusBar().addPermanentWidget(
            self.y_status
        )
        self.statusBar().addPermanentWidget(
            self.depth_status
        )
        self.statusBar().addPermanentWidget(
            self.curve_value_status
        )
        self.statusBar().addPermanentWidget(
            self.zoom_status
        )

    def _connect_signals(self) -> None:
        self.project_panel.segmentSelected.connect(
            self.select_segment
        )
        self.project_panel.workflowNodeActivated.connect(
            self._project_tree_node_activated
        )
        self.preprocess_panel.enhancementSettingsChanged.connect(
            self.live_enhancement.set_settings
        )
        self.project_panel.addImagesRequested.connect(
            self.add_images
        )
        self.project_panel.removeRequested.connect(
            self.remove_segment
        )
        self.project_panel.moveUpRequested.connect(
            lambda segment_id:
            self.move_segment(
                segment_id,
                -1,
            )
        )
        self.project_panel.moveDownRequested.connect(
            lambda segment_id:
            self.move_segment(
                segment_id,
                1,
            )
        )

        self.viewer.cursorPositionChanged.connect(
            self._update_coordinates
        )
        self.viewer.visibleCenterImageChanged.connect(
            self._update_navigation_hud_from_center_signal
        )
        self.viewer.zoomChanged.connect(
            self._update_zoom
        )
        self.viewer.rectificationPointsChanged.connect(
            self._rectification_points_changed
        )
        self.viewer.rectificationValidationChanged.connect(
            self._rectification_validation_changed
        )
        self.viewer.rectificationControlPairsChanged.connect(
            self._rectification_control_pairs_changed
        )
        self.viewer.rectificationMultipointValidationChanged.connect(
            self._rectification_multipoint_validation_changed
        )

        self.preprocess_panel.rectificationModeToggled.connect(
            self._set_rectification_capture_mode
        )
        self.preprocess_panel.multipointModeToggled.connect(
            self._set_multipoint_rectification_mode
        )
        self.preprocess_panel.addControlPairRequested.connect(
            self._add_rectification_control_pair
        )
        self.preprocess_panel.removeControlPairRequested.connect(
            self._remove_rectification_control_pair
        )
        self.preprocess_panel.clearRequested.connect(
            self.viewer.clear_rectification_points
        )
        self.preprocess_panel.previewRequested.connect(
            self.preview_rectification
        )
        self.preprocess_panel.applyRequested.connect(
            self.apply_rectification
        )
        self.preprocess_panel.enhancementApplyRequested.connect(
            self.apply_image_enhancement
        )

        self.calibration_panel.captureModeToggled.connect(
            self._set_depth_capture_mode
        )
        self.calibration_panel.clearRequested.connect(
            self._clear_depth_points
        )
        self.calibration_panel.deleteRequested.connect(
            self._delete_depth_point
        )
        self.calibration_panel.editRequested.connect(
            self._edit_depth_point
        )
        self.calibration_panel.unitChanged.connect(
            self._depth_unit_changed
        )
        self.calibration_panel.horizontalCaptureModeToggled.connect(
            self._set_horizontal_capture_mode
        )
        self.viewer.depthPointRequested.connect(
            self._add_depth_point_requested
        )
        self.viewer.depthCalibrationPointMoved.connect(
            self._depth_point_moved
        )
        self.viewer.depthCalibrationEditRequested.connect(
            self._edit_depth_point
        )

        # Digitization readiness depends on curve definitions, curve presence and
        # calibration state. These refreshes run after the existing handlers and
        # keep the separate Digitization tab synchronized without coupling its
        # panel to calibration implementation details.
        for signal in (
            self.calibration_panel.curveAddRequested,
            self.calibration_panel.curveEditRequested,
            self.calibration_panel.curveRemoveRequested,
            self.calibration_panel.curveSelectionChanged,
            self.calibration_panel.curvePresenceChanged,
            self.calibration_panel.horizontalClearRequested,
            self.calibration_panel.horizontalDeleteRequested,
            self.calibration_panel.horizontalEditRequested,
            self.viewer.horizontalPointRequested,
            self.viewer.horizontalCalibrationPointMoved,
        ):
            signal.connect(lambda *_args: self.manual_digitization_controller.refresh())

        # The HUD shows the scale/offset the user is actively working with.
        # Remember whether Calibration or Digitization last changed that context
        # so a stale offset from the other tab cannot silently dominate the HUD.
        for signal in (
            self.calibration_panel.curveSelectionChanged,
            self.calibration_panel.horizontalScaleSelectionChanged,
        ):
            signal.connect(
                lambda *_args: self._set_hud_scale_context_source("calibration")
            )
        for signal in (
            self.digitization_panel.curveSelectionChanged,
            self.digitization_panel.scaleSelectionChanged,
            self.digitization_panel.scaleOffsetChanged,
        ):
            signal.connect(
                lambda *_args: self._set_hud_scale_context_source("digitization")
            )

        # Keep Project Explorer visually synchronized with the curve the user
        # chooses in the Digitization workspace. This changes only tree focus:
        # project/scientific data are not touched.
        self.digitization_panel.curveSelectionChanged.connect(
            self._sync_project_explorer_to_digitization_curve
        )
        self.calibration_panel.curveSelectionChanged.connect(
            self._sync_project_explorer_to_calibration_curve
        )

    def _prepare_manual_digitization_capture(self) -> None:
        """Deactivate other left-click capture tools before digitization."""
        if (
            hasattr(self, "automatic_digitization_controller")
            and self.automatic_digitization_controller.active
        ):
            self.automatic_digitization_controller.abort_for_context_change()
        if self.preprocess_panel.mode_check.isChecked():
            self.preprocess_panel.mode_check.setChecked(False)
        if self.calibration_panel.capture_check.isChecked():
            self.calibration_panel.set_capture_mode(False)
        if self.calibration_panel.x_capture_check.isChecked():
            self.calibration_panel.set_horizontal_capture_mode(False)

    def _set_rectification_capture_mode(self, enabled: bool) -> None:
        """Keep rectification and other point-capture tools mutually exclusive."""
        if (
            enabled
            and hasattr(self, "automatic_digitization_controller")
            and self.automatic_digitization_controller.active
        ):
            self.preprocess_panel.mode_check.setChecked(False)
            self.statusBar().showMessage(
                "Clear the current automatic trace first.",
                5000,
            )
            return
        if enabled and self.manual_digitization_controller.canvas_capture_active:
            self.preprocess_panel.mode_check.setChecked(False)
            self.statusBar().showMessage(
                "Finish or turn off the current digitization tool first.",
                5000,
            )
            return
        if enabled and self.calibration_panel.capture_check.isChecked():
            self.calibration_panel.set_capture_mode(False)
        if enabled and self.calibration_panel.x_capture_check.isChecked():
            self.calibration_panel.set_horizontal_capture_mode(False)
        self.viewer.set_rectification_enabled(enabled)

    def _set_multipoint_rectification_mode(self, enabled: bool) -> None:
        """Select standard or advanced rectification without deleting controls."""
        if not enabled:
            self.viewer.set_rectification_control_pair_capture_enabled(False)
            return

        if not self.viewer.rectification.is_complete:
            self.preprocess_panel.multipoint_check.setChecked(False)
            self.statusBar().showMessage(
                "Define four valid outer corners before multipoint correction.",
                5000,
            )
            return

        self.statusBar().showMessage(
            "Multipoint correction enabled. Use Add control pair for each "
            "additional transverse reference.",
            5000,
        )

    def _add_rectification_control_pair(self) -> None:
        """Arm one left-then-right intermediate control-pair capture."""
        if not self.preprocess_panel.multipoint_enabled:
            return

        if not self.preprocess_panel.mode_check.isChecked():
            self.preprocess_panel.mode_check.setChecked(True)

        # The normal capture-mode handler may reject activation while another
        # digitization tool is busy. In that case do not arm pair capture.
        if not self.preprocess_panel.mode_check.isChecked():
            return

        if not self.viewer.set_rectification_control_pair_capture_enabled(True):
            self.statusBar().showMessage(
                "Define four outer rectification corners first.",
                5000,
            )
            return

        self.statusBar().showMessage(
            "Add control pair: click LEFT boundary, then the corresponding "
            "RIGHT boundary.",
            7000,
        )

    def _remove_rectification_control_pair(self) -> None:
        """Remove the most recently added intermediate pair."""
        if not self.viewer.remove_last_rectification_control_pair():
            self.statusBar().showMessage(
                "There are no intermediate control pairs to remove.",
                3500,
            )

    def _set_depth_capture_mode(self, enabled: bool) -> None:
        """Enable depth-point capture only on the rectified working geometry."""
        if (
            enabled
            and hasattr(self, "automatic_digitization_controller")
            and self.automatic_digitization_controller.active
        ):
            self.calibration_panel.set_capture_mode(False)
            self.statusBar().showMessage(
                "Clear the current automatic trace first.",
                5000,
            )
            return
        if enabled and self.manual_digitization_controller.canvas_capture_active:
            self.calibration_panel.set_capture_mode(False)
            self.statusBar().showMessage(
                "Finish or turn off the current digitization tool first.",
                5000,
            )
            return
        if enabled:
            if not self._calibration_image_ready():
                self.calibration_panel.set_capture_mode(False)
                self.viewer.set_depth_calibration_enabled(False)
                self.statusBar().showMessage(
                    "Apply rectification before adding depth calibration points.",
                    5000,
                )
                return
            if self.preprocess_panel.mode_check.isChecked():
                self.preprocess_panel.mode_check.setChecked(False)
            if self.calibration_panel.x_capture_check.isChecked():
                self.calibration_panel.set_horizontal_capture_mode(False)
        self.viewer.set_depth_calibration_enabled(enabled)

    def _set_horizontal_capture_mode(self, enabled: bool) -> None:
        """Keep X-point capture mutually exclusive with all other capture tools."""
        if (
            enabled
            and hasattr(self, "automatic_digitization_controller")
            and self.automatic_digitization_controller.active
        ):
            self.calibration_panel.set_horizontal_capture_mode(False)
            self.statusBar().showMessage(
                "Clear the current automatic trace first.",
                5000,
            )
            return
        if enabled and self.manual_digitization_controller.canvas_capture_active:
            self.calibration_panel.set_horizontal_capture_mode(False)
            self.statusBar().showMessage(
                "Finish or turn off the current digitization tool first.",
                5000,
            )
            return
        if enabled:
            if not self.curve_calibration_controller.can_capture():
                self.calibration_panel.set_horizontal_capture_mode(False)
                self.viewer.set_horizontal_calibration_enabled(False)
                self.statusBar().showMessage(
                    self.curve_calibration_controller.capture_unavailable_message(),
                    5000,
                )
                return
            if self.preprocess_panel.mode_check.isChecked():
                self.preprocess_panel.mode_check.setChecked(False)
            if self.calibration_panel.capture_check.isChecked():
                self.calibration_panel.set_capture_mode(False)
        self.viewer.set_horizontal_calibration_enabled(enabled)

    def _visible_depth_marker_positions(
        self,
        points,
    ) -> dict[float, float]:
        """Read current presentation X coordinates keyed by physical depth."""
        positions: dict[float, float] = {}
        for index, point in enumerate(points):
            x_px = self.viewer.depth_calibration.point_image_x(index)
            if x_px is not None:
                positions[float(point.depth)] = float(x_px)
        return positions

    def _persist_visible_calibration_marker_positions(self) -> bool:
        """Persist visible T-marker presentation coordinates for this image.

        Only the complementary UI coordinates are stored: X for depth markers
        and Y for curve markers. Scientific calibration values are untouched.
        """
        changed = False
        segment = self.service.active_segment
        if segment is not None and self.viewer.depth_calibration.handles:
            points = self.service.get_depth_calibration_points(segment)
            positions = self._visible_depth_marker_positions(points)
            if positions:
                changed = (
                    self.service.set_depth_calibration_marker_positions(
                        positions,
                        segment,
                    )
                    or changed
                )

        if hasattr(self, "curve_calibration_controller"):
            changed = (
                self.curve_calibration_controller.persist_visible_marker_positions()
                or changed
            )
        if changed:
            self._refresh_title()
        return changed

    def _add_depth_point_requested(self, x: float, y: float) -> None:
        if not self._calibration_image_ready():
            return

        unit = self.service.get_depth_calibration_unit()
        dialog = DepthValueDialog(
            unit,
            title="Add depth calibration point",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        depth = dialog.depth_value
        try:
            self.service.add_depth_calibration_point(
                pixel_y=y,
                depth=depth,
            )
        except (CalibrationError, ValueError) as exc:
            self._show_error("Cannot add depth point", exc)
            return

        self._refresh_depth_calibration(
            preferred_x_by_depth={depth: x}
        )
        self._refresh_project_state()
        self.statusBar().showMessage(
            f"Depth reference added: {depth:g} "
            f"{self._depth_unit_text(unit)}",
            3500,
        )

    def _depth_point_moved(
        self,
        index: int,
        x: float,
        y: float,
    ) -> None:
        points = self.service.get_depth_calibration_points()
        if not 0 <= index < len(points):
            self._refresh_depth_calibration()
            return

        # Capture presentation coordinates against the OLD physical depths
        # before a scientific Y move can reorder points by pixel_y.
        marker_positions = self._visible_depth_marker_positions(points)
        marker_positions[float(points[index].depth)] = float(x)

        if abs(points[index].pixel_y - y) <= 1e-6:
            # Pure horizontal movement is persisted as UI state only. It does
            # not alter scientific depth calibration or downstream validity.
            if self.service.set_depth_calibration_marker_positions(
                marker_positions
            ):
                self._refresh_title()
            return

        try:
            self.service.update_depth_calibration_point(
                index,
                pixel_y=y,
            )
        except (CalibrationError, IndexError, ValueError) as exc:
            # Repaint from persisted data to undo an invalid drag, for example a
            # move that would break strict depth monotonicity.
            self._refresh_depth_calibration()
            self.statusBar().showMessage(
                f"Depth point move rejected: {exc}",
                6000,
            )
            return

        self.service.set_depth_calibration_marker_positions(
            marker_positions
        )
        self._refresh_depth_calibration()
        self._refresh_project_state()


    def _edit_depth_point(self, index: int) -> None:
        points = self.service.get_depth_calibration_points()
        if not 0 <= index < len(points):
            return

        point = points[index]
        unit = self.service.get_depth_calibration_unit()
        x_hint = self.viewer.depth_calibration.point_image_x(index)
        dialog = DepthValueDialog(
            unit,
            initial_depth=point.depth,
            title="Edit depth calibration point",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_depth = dialog.depth_value
        try:
            self.service.update_depth_calibration_point(
                index,
                depth=new_depth,
            )
        except (CalibrationError, IndexError, ValueError) as exc:
            self._show_error("Cannot edit depth point", exc)
            return

        preferred = (
            {new_depth: x_hint}
            if x_hint is not None
            else None
        )
        self._refresh_depth_calibration(
            preferred_x_by_depth=preferred
        )
        self._refresh_project_state()

    def _delete_depth_point(self, index: int) -> None:
        try:
            self.service.remove_depth_calibration_point(index)
        except (CalibrationError, IndexError, ValueError) as exc:
            self._show_error("Cannot delete depth point", exc)
            return

        self._refresh_depth_calibration()
        self._refresh_project_state()

    def _clear_depth_points(self) -> None:
        points = self.service.get_depth_calibration_points()
        if not points:
            return

        response = QMessageBox.question(
            self,
            "Clear depth calibration",
            "Remove all depth calibration points from this segment?",
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        self.service.clear_depth_calibration_points()
        self._refresh_depth_calibration()
        self._refresh_project_state()

    def _depth_unit_changed(self, unit: DepthUnit) -> None:
        try:
            self.service.set_depth_calibration_unit(unit)
        except (CalibrationError, RuntimeError, ValueError) as exc:
            self._show_error("Cannot change depth unit", exc)
            self._refresh_depth_calibration()
            return

        self._refresh_depth_calibration()
        self._refresh_project_state()

    def _refresh_depth_calibration(
        self,
        *,
        preferred_x_by_depth: dict[float, float] | None = None,
    ) -> None:
        """Synchronize panel, overlay and real-time cursor conversion."""
        segment = self.service.active_segment
        if segment is None:
            self.viewer.clear_depth_calibration_points()
            self.calibration_panel.set_points((), DepthUnit.METERS)
            self.calibration_panel.set_image_available(False)
            self._active_depth_calibration = None
            self.depth_status.setText("Depth: —")
            self.viewer.clear_image_overlap_guides()
            self.curve_calibration_controller.refresh()
            self.viewer.clear_navigation_hud()
            return

        points = self.service.get_depth_calibration_points(segment)
        unit = self.service.get_depth_calibration_unit(segment)
        self.calibration_panel.set_points(points, unit)

        image_ready = self._calibration_image_ready(segment)
        self.calibration_panel.set_image_available(image_ready)

        if image_ready and self.viewer.has_image:
            # Restore the complementary presentation X coordinate from the
            # project UI metadata. A just-created/edited point supplies an
            # explicit preferred position which wins and is persisted.
            persisted_x = self.service.get_depth_calibration_marker_positions(
                segment
            )
            marker_x = dict(persisted_x)
            if preferred_x_by_depth:
                marker_x.update(
                    {
                        float(depth): float(x_px)
                        for depth, x_px in preferred_x_by_depth.items()
                    }
                )
                if self.service.set_depth_calibration_marker_positions(
                    marker_x,
                    segment,
                ):
                    self._refresh_title()

            self.viewer.set_depth_calibration_points(
                points,
                unit,
                preferred_x_by_depth=marker_x or None,
            )
        else:
            self.viewer.clear_depth_calibration_points()

        self._active_depth_calibration = self.service.get_depth_calibration(
            segment
        )
        self.depth_status.setText(
            "Depth: Not calibrated"
            if self._active_depth_calibration is None
            else "Depth: move cursor over image"
        )

        # Step 7.4 QC depends on both Y and the selected curve's X state.
        # Refresh it immediately after every vertical calibration change.
        if hasattr(self, "curve_calibration_controller"):
            self.curve_calibration_controller.refresh()
        if hasattr(self, "manual_digitization_controller"):
            self.manual_digitization_controller.refresh()
        self._refresh_image_overlap_guides()
        self._update_navigation_hud_from_center()


    def _calibration_image_ready(self, segment=None) -> bool:
        target = segment or self.service.active_segment
        if target is None:
            return False
        path = self.service.resolve_segment_preprocessed(target)
        return path is not None and path.is_file()

    @staticmethod
    def _depth_unit_text(unit: DepthUnit) -> str:
        return "m" if unit is DepthUnit.METERS else "ft"

    def new_project(self) -> None:
        if not self._confirm_unsaved_changes():
            return

        dialog = NewProjectDialog(self)
        if (
            dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        values = dialog.values
        self.manual_digitization_controller.abort_for_context_change()
        self.automatic_digitization_controller.abort_for_context_change()

        try:
            self.service.new_project(
                name=values["name"],
                project_directory=Path(
                    values["project_directory"]
                ),
                well_name=values["well_name"],
                description=values["description"],
            )
        except (
            LogDigitizerError,
            OSError,
        ) as exc:
            self._show_error(
                "Cannot create project",
                exc,
            )
            return

        self._record_current_project_as_recent()
        self._refresh_all()

        # A new project cannot be used meaningfully until at least one source
        # image is available. Prompt for source images immediately after the
        # project has been created, while keeping Cancel as a valid choice so
        # an intentionally empty project remains possible. The same picker and
        # import workflow is reused by the persistent Add Image action.
        image_paths = self._choose_source_image_paths()
        if not image_paths:
            self.statusBar().showMessage(
                "Project created",
                4000,
            )
            return

        added = self._import_image_paths(image_paths)
        if added is None:
            # The project itself was created successfully; the import error was
            # already reported by the shared helper. Do not mask it with a
            # second status message.
            return

        self.statusBar().showMessage(
            f"Project created · Imported {len(added)} image(s)",
            5000,
        )

    def open_project(self) -> None:
        if not self._confirm_unsaved_changes():
            return

        filename, _ = (
            QFileDialog.getOpenFileName(
                self,
                "Open Log Project",
                "",
                "Log Project (*.logproj);;All files (*)",
            )
        )
        if not filename:
            return
        self._open_project_file(Path(filename), confirm_unsaved=False)

    def _open_recent_project(self, project_file: Path) -> None:
        """Open one remembered project without changing its save-order rank."""
        path = Path(project_file).expanduser().resolve()
        if not path.is_file():
            self.recent_projects.remove_project(path)
            self._refresh_recent_projects_ui()
            QMessageBox.warning(
                self,
                "Recent Project Not Found",
                (
                    "The project file could not be found and has been removed "
                    f"from Recent Projects.\n\n{path}"
                ),
            )
            return
        self._open_project_file(path, confirm_unsaved=True)

    def _open_project_file(
        self,
        project_file: Path,
        *,
        confirm_unsaved: bool,
    ) -> None:
        """Open a chosen .logproj path through one shared GUI workflow."""
        if confirm_unsaved and not self._confirm_unsaved_changes():
            return

        self.manual_digitization_controller.abort_for_context_change()
        self.automatic_digitization_controller.abort_for_context_change()
        try:
            self.service.open_project(project_file)
        except (
            LogDigitizerError,
            OSError,
        ) as exc:
            logger.exception(
                "Failed to open project %s",
                project_file,
            )
            self._show_error(
                "Cannot open project",
                exc,
            )
            return

        self._refresh_all()
        self._load_active_segment()
        self.curve_calibration_controller.refresh()
        self.statusBar().showMessage(
            "Project opened",
            4000,
        )

    def save_project(self) -> None:
        self._persist_visible_calibration_marker_positions()
        try:
            self.service.save()
        except (
            LogDigitizerError,
            OSError,
        ) as exc:
            logger.exception(
                "Failed to save project"
            )
            self._show_error(
                "Cannot save project",
                exc,
            )
            return

        self._record_current_project_as_recent()
        self._refresh_title()
        self._refresh_recent_projects_ui()
        self.statusBar().showMessage(
            "Project saved",
            3000,
        )

    def open_quality_control(
        self,
        *,
        curve_ids: tuple[str, ...] | None = None,
    ) -> None:
        """Open or focus the modeless project-level Quality Control window."""
        if not self.service.has_project or not self.service.can_export_las():
            QMessageBox.information(
                self,
                "Quality Control",
                "No digitized curves are ready for quality control.",
            )
            return

        dialog = self._quality_control_dialog
        if dialog is not None:
            dialog.set_curve_filter(curve_ids)
            dialog.sync_step_from_project()
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return

        dialog = QualityControlDialog(
            self.service,
            self,
            curve_ids=curve_ids,
        )
        dialog.navigationRequested.connect(
            self._navigate_to_quality_control_issue
        )
        dialog.stepChanged.connect(
            self._quality_control_step_changed
        )
        dialog.destroyed.connect(
            self._quality_control_dialog_destroyed
        )
        self._quality_control_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _quality_control_step_changed(self, _step: float) -> None:
        """Synchronize project state and the visual LAS preview after QC STEP edits."""
        self._refresh_project_state()
        if self._las_curve_preview_dialog is not None:
            self._las_curve_preview_dialog.sync_step_from_project()

    def open_las_curve_preview(self) -> None:
        """Open or focus the modeless final LAS curve reconstruction preview."""
        if not self.service.has_project or not self.service.can_export_las():
            QMessageBox.information(
                self,
                "LAS Curve Preview",
                "No digitized curves are ready for LAS curve preview.",
            )
            return

        dialog = self._las_curve_preview_dialog
        if dialog is not None:
            dialog.sync_step_from_project()
            dialog.refresh_from_project()
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return

        dialog = LasCurvePreviewDialog(
            self.service,
            self,
        )
        dialog.navigationRequested.connect(
            self._navigate_from_las_curve_preview
        )
        dialog.stepChanged.connect(
            self._las_curve_preview_step_changed
        )
        dialog.destroyed.connect(
            self._las_curve_preview_dialog_destroyed
        )
        self._las_curve_preview_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _las_curve_preview_step_changed(self, _step: float) -> None:
        """Synchronize STEP-dependent windows after LAS preview edits."""
        self._refresh_project_state()
        if self._quality_control_dialog is not None:
            self._quality_control_dialog.sync_step_from_project()

    def _navigate_from_las_curve_preview(
        self,
        curve_id: str,
        segment_id: str,
        depth: float,
    ) -> None:
        """Navigate to a preview marker without changing the selected curve.

        The source segment may change because the requested depth belongs to a
        different scanned image. The active curve/tool context is deliberately
        left untouched: automatic traces and other non-selectable curve states
        must remain valid.
        """
        if self.service.project is None:
            return

        if self.service.active_segment_id != segment_id:
            self.select_segment(segment_id)
            if self.service.active_segment_id != segment_id:
                return

        segment = self.service.active_segment
        if segment is None or not self.viewer.has_image:
            return

        calibration = self.service.get_depth_calibration(segment)
        if calibration is None:
            return

        target_depth = min(
            max(
                float(depth),
                float(calibration.minimum_depth),
            ),
            float(calibration.maximum_depth),
        )
        try:
            target_y = float(
                depth_to_pixel_y(target_depth, calibration)
            )
        except (CalibrationError, CalibrationRangeError):
            return

        _width, image_height = self.viewer.image_size
        if image_height <= 0:
            return

        # Use a deliberately tight local window around the marker so nearby
        # scale/offset transitions can be distinguished clearly. The interval
        # remains proportional to image height, but is capped to avoid an
        # overly distant view on long scanned logs.
        half_window_px = min(
            55.0,
            max(22.0, float(image_height) * 0.02),
        )
        focus_start_y = max(0.0, target_y - half_window_px)
        focus_stop_y = min(
            float(image_height - 1),
            target_y + half_window_px,
        )

        center_x = self._quality_control_curve_x_near_y(
            curve_id,
            target_y,
        )
        self.viewer.focus_image_interval(
            y_start_px=focus_start_y,
            y_stop_px=focus_stop_y,
            anchor_y_px=target_y,
            center_x_px=center_x,
            tight_focus=True,
        )

        # Do not refresh controllers with curve_id and do not focus a curve in
        # Project Explorer. This operation is navigation only.
        self.raise_()
        self.activateWindow()
        self.statusBar().showMessage(
            f"LAS Curve Preview: {target_depth:g} "
            f"{self._depth_unit_text(calibration.unit)}",
            5000,
        )

    def _las_curve_preview_dialog_destroyed(self, *_args) -> None:
        self._las_curve_preview_dialog = None

    def _quality_control_dialog_destroyed(self, *_args) -> None:
        self._quality_control_dialog = None

    def _navigate_to_quality_control_issue(
        self,
        curve_id: str,
        segment_id: str,
        depth: float,
        focus_start_depth: float,
        focus_stop_depth: float,
        tight_focus: bool,
    ) -> None:
        """Select the relevant image/curve and zoom to one QC gap."""
        if self.service.project is None:
            return

        if self.service.active_segment_id != segment_id:
            self.select_segment(segment_id)
            if self.service.active_segment_id != segment_id:
                return

        self.curve_calibration_controller.refresh(
            selected_curve_id=curve_id
        )
        self.manual_digitization_controller.refresh(
            selected_curve_id=curve_id
        )
        self.digitization_dock.raise_()
        self.project_panel.focus_digitization_curve(
            segment_id,
            curve_id,
        )

        segment = self.service.active_segment
        if segment is None or not self.viewer.has_image:
            return
        calibration = self.service.get_depth_calibration(segment)
        if calibration is None:
            return

        minimum_depth = float(calibration.minimum_depth)
        maximum_depth = float(calibration.maximum_depth)

        def clamp_depth(value: float) -> float:
            return min(
                max(float(value), minimum_depth),
                maximum_depth,
            )

        try:
            target_y = depth_to_pixel_y(
                clamp_depth(depth),
                calibration,
            )
            focus_start_y = depth_to_pixel_y(
                clamp_depth(focus_start_depth),
                calibration,
            )
            focus_stop_y = depth_to_pixel_y(
                clamp_depth(focus_stop_depth),
                calibration,
            )
        except (CalibrationError, CalibrationRangeError):
            return

        center_x = self._quality_control_gap_center_x(
            curve_id,
            float(focus_start_y),
            float(focus_stop_y),
            fallback_y_px=float(target_y),
        )
        self.viewer.focus_image_interval(
            y_start_px=float(focus_start_y),
            y_stop_px=float(focus_stop_y),
            anchor_y_px=float(target_y),
            center_x_px=center_x,
            tight_focus=bool(tight_focus),
        )

        # Bring the working canvas in front of the modeless QC window so the
        # navigation command behaves like an actual "go to" operation.
        self.raise_()
        self.activateWindow()
        self.statusBar().showMessage(
            f"Quality Control: {depth:g} "
            f"{self._depth_unit_text(calibration.unit)}",
            5000,
        )

    def _quality_control_gap_center_x(
        self,
        curve_id: str,
        first_y_px: float,
        second_y_px: float,
        *,
        fallback_y_px: float,
    ) -> float | None:
        """Center QC on the real trace endpoints that bound a gap."""
        xs: list[float] = []
        for y_px in (first_y_px, second_y_px):
            x_px = self._quality_control_curve_x_near_y(curve_id, y_px)
            if x_px is not None:
                xs.append(x_px)

        if len(xs) >= 2:
            return (xs[0] + xs[1]) / 2.0
        if xs:
            return xs[0]
        return self._quality_control_curve_x_near_y(
            curve_id,
            fallback_y_px,
        )

    def _quality_control_curve_x_near_y(
        self,
        curve_id: str,
        target_y_px: float,
    ) -> float | None:
        """Return the actual curve X at, or vertically nearest to, one image Y."""
        target_scene = self.viewer.image_to_scene(
            QPointF(0.0, float(target_y_px))
        )
        target_y = float(target_scene.y())
        eps = 1e-7 * max(1.0, abs(target_y))

        intersections: list[float] = []
        nearest_x: float | None = None
        nearest_dy = float("inf")

        for item in self.viewer.saved_digitized_curves.path_items:
            if str(getattr(item, "curve_id", "")) != str(curve_id):
                continue
            getter = getattr(item, "path", None)
            if not callable(getter):
                continue

            for polygon in getter().toSubpathPolygons(item.sceneTransform()):
                points = list(polygon)
                for point in points:
                    dy = abs(float(point.y()) - target_y)
                    if dy < nearest_dy:
                        nearest_dy = dy
                        nearest_x = float(point.x())

                for p0, p1 in zip(points, points[1:]):
                    x0, y0 = float(p0.x()), float(p0.y())
                    x1, y1 = float(p1.x()), float(p1.y())
                    if not min(y0, y1) - eps <= target_y <= max(y0, y1) + eps:
                        continue
                    if abs(y1 - y0) <= eps:
                        intersections.append((x0 + x1) / 2.0)
                    else:
                        t = (target_y - y0) / (y1 - y0)
                        t = min(max(t, 0.0), 1.0)
                        intersections.append(x0 + t * (x1 - x0))

        scene_x = None
        if intersections:
            intersections.sort()
            scene_x = intersections[len(intersections) // 2]
        elif nearest_x is not None:
            scene_x = nearest_x

        if scene_x is None:
            return None

        image_point = self.viewer.scene_to_image(
            QPointF(float(scene_x), target_y)
        )
        return None if image_point is None else float(image_point.x())


    def export_las(self) -> None:
        """Open the project-level LAS export workflow."""
        if not self.service.has_project or not self.service.can_export_las():
            QMessageBox.information(
                self,
                "Export LAS",
                "No digitized curves are ready for LAS export.",
            )
            return

        dialog = LasExportDialog(self.service, self)
        result = dialog.exec()

        # STEP can now be edited without exporting, so refresh project dirty state
        # regardless of how the modal export dialog was closed.
        self._refresh_project_state()
        if self._quality_control_dialog is not None:
            self._quality_control_dialog.sync_step_from_project()
        if self._las_curve_preview_dialog is not None:
            self._las_curve_preview_dialog.sync_step_from_project()

        if dialog.quality_control_requested:
            self.open_quality_control(
                curve_ids=dialog.quality_control_curve_ids
            )
            return

        if result != QDialog.DialogCode.Accepted:
            return

        if dialog.exported_path is None or dialog.export_plan is None:
            return
        self.statusBar().showMessage(
            f"LAS exported: {dialog.exported_path} · "
            f"{len(dialog.export_plan.columns)} curve(s) · "
            f"{dialog.export_plan.row_count:,} rows",
            7000,
        )

    def _choose_source_image_paths(self) -> tuple[Path, ...]:
        """Open the shared multi-image picker used by project creation/Add Image."""
        suffix_text = " ".join(
            f"*{suffix}"
            for suffix in sorted(
                SUPPORTED_IMAGE_SUFFIXES
            )
        )
        filters = (
            f"Images ({suffix_text});;"
            "All files (*)"
        )
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Add source images",
            "",
            filters,
        )
        return tuple(Path(filename) for filename in filenames)

    def _import_image_paths(self, image_paths: tuple[Path, ...]) -> list | None:
        """Import chosen images and activate the last one through one workflow.

        ``None`` means the import failed and the error has already been shown.
        An empty list is a valid successful no-op, although callers normally do
        not invoke this helper with an empty selection.
        """
        try:
            added = self.service.add_images(list(image_paths))
        except (
            LogDigitizerError,
            OSError,
            RuntimeError,
        ) as exc:
            logger.exception(
                "Failed to import one or more images"
            )
            self._show_error(
                "Cannot add image",
                exc,
            )
            self._refresh_all()
            return None

        self._refresh_all()

        if added:
            self.project_panel.select_segment(
                added[-1].segment_id
            )
            self._load_active_segment()
            self.curve_calibration_controller.refresh()

        return added

    def add_images(self) -> None:
        """Add one or more source images to the current project."""
        if not self.service.has_project:
            QMessageBox.information(
                self,
                "Add Image",
                "Create or open a project first.",
            )
            return

        image_paths = self._choose_source_image_paths()
        if not image_paths:
            return

        added = self._import_image_paths(image_paths)
        if added is None:
            return

        self.statusBar().showMessage(
            f"Imported {len(added)} image(s)",
            4000,
        )

    def select_segment(
        self,
        segment_id: str,
    ) -> None:
        # ``load_image`` clears graphical overlays. Persist their complementary
        # UI coordinates before changing the active image so the exact T anchors
        # can be reconstructed when this image is revisited.
        self._persist_visible_calibration_marker_positions()
        if hasattr(self, "manual_digitization_controller"):
            self.manual_digitization_controller.abort_for_context_change()
        if hasattr(self, "automatic_digitization_controller"):
            self.automatic_digitization_controller.abort_for_context_change()
        try:
            self.service.select_segment(
                segment_id
            )
        except KeyError:
            return

        self._load_active_segment()
        self.curve_calibration_controller.refresh()
        self.manual_digitization_controller.refresh()
        self._refresh_project_panel()
        self._refresh_image_overlap_guides()

    def remove_segment(
        self,
        segment_id: str,
    ) -> None:
        segment = (
            self.service.project.get_segment(
                segment_id
            )
            if self.service.project
            else None
        )
        if segment is None:
            return

        response = QMessageBox.question(
            self,
            "Remove segment",
            (
                f"Remove '{segment.name}' from the project?\n\n"
                "The copied source image will be kept on disk "
                "for traceability."
            ),
            (
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
            ),
            QMessageBox.StandardButton.No,
        )

        if (
            response
            != QMessageBox.StandardButton.Yes
        ):
            return

        self.manual_digitization_controller.abort_for_context_change()
        self.automatic_digitization_controller.abort_for_context_change()
        self.service.remove_segment(
            segment_id
        )
        self._refresh_all()
        self._load_active_segment()
        self.curve_calibration_controller.refresh()

    def move_segment(
        self,
        segment_id: str,
        offset: int,
    ) -> None:
        if self.service.move_segment(
            segment_id,
            offset,
        ):
            self._refresh_all()
            self.project_panel.select_segment(
                segment_id
            )

    def preview_rectification(self) -> None:
        """Generate and show a non-destructive standard or multipoint preview."""
        try:
            result = (
                self.viewer
                .compute_rectification_preview(
                    multipoint=self.preprocess_panel.multipoint_enabled
                )
            )
        except (
            InvalidQuadrilateralError,
            ValueError,
        ) as exc:
            self._show_error(
                "Cannot preview rectification",
                exc,
            )
            return

        dialog = RectificationPreviewDialog(
            result.image,
            result.transform.output_size,
            self,
        )
        dialog.exec()
    
    def apply_rectification(self) -> None:
        """Generate and persist the active segment's rectified derivative."""
        if (
            not self.service.has_project
            or self.service.active_segment is None
        ):
            QMessageBox.information(
                self,
                "Apply Rectification",
                "Select a project segment first.",
            )
            return

        try:
            result = (
                self.viewer
                .compute_rectification_preview(
                    multipoint=self.preprocess_panel.multipoint_enabled
                )
            )
            saved_path = (
                self.service
                .apply_rectification(
                    result.image,
                    result.transform,
                )
            )
        except (
            InvalidQuadrilateralError,
            LogDigitizerError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            logger.exception(
                "Failed to apply rectification"
            )
            self._show_error(
                "Cannot apply rectification",
                exc,
            )
            return

        relative = (
            self.service.active_segment
            .extensions["preprocess"]["derived_path"]
        )
        # La rectificación aceptada se convierte en la imagen base de trabajo.
        self.viewer.set_working_image(saved_path)

        self.live_enhancement.set_base_image(
            self.viewer.source_image_array(),
            reset_settings=True,
        )

        self.preprocess_panel.set_enhancement_settings(
            self.live_enhancement.settings
        )

        self.preprocess_panel.enter_enhancement_stage(relative)
        self._refresh_depth_calibration()
        self.curve_calibration_controller.refresh()
        self._refresh_all()
        self.statusBar().showMessage(
            f"Rectification applied: {saved_path.name}",
            5000,
        )

    def apply_image_enhancement(self) -> None:
        """Persist current live enhancement as the final working image."""
        if (
            not self.service.has_project
            or self.service.active_segment is None
        ):
            QMessageBox.information(
                self,
                "Apply Image Enhancement",
                "Select a project segment first.",
            )
            return

        if not self.live_enhancement.has_base_image:
            QMessageBox.information(
                self,
                "Apply Image Enhancement",
                "Apply geometric rectification before image enhancement.",
            )
            return

        try:
            enhanced_image = (
                self.live_enhancement
                .enhanced_image()
            )
            saved_path = (
                self.service
                .apply_enhancement(
                    enhanced_image,
                    self.preprocess_panel.enhancement_settings,
                )
            )
        except (
            LogDigitizerError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            logger.exception(
                "Failed to apply image enhancement"
            )
            self._show_error(
                "Cannot apply image enhancement",
                exc,
            )
            return

        relative = (
            self.service.active_segment
            .extensions["preprocess"]["working_path"]
        )
        self.preprocess_panel.set_enhancement_applied_path(
            relative
        )
        self._refresh_all()
        self.statusBar().showMessage(
            f"Working image saved: {saved_path.name}",
            5000,
        )

    def _load_active_segment(self) -> None:
        segment = self.service.active_segment

        if segment is None:
            self.viewer.clear_image()
            self.live_enhancement.clear_base_image()
            self.segment_status.setText(
                "Segment: —"
            )
            self.preprocess_panel.set_image_available(
                False
            )
            self.viewer.clear_depth_calibration_points()
            self.calibration_panel.set_points((), DepthUnit.METERS)
            self.calibration_panel.set_image_available(False)
            self._active_depth_calibration = None
            self.depth_status.setText("Depth: —")
            if hasattr(self, "manual_digitization_controller"):
                self.manual_digitization_controller.refresh()
            return

        self.segment_status.setText(
            f"Segment: {segment.order:02d} — {segment.name}"
        )

        rectified_path = self.service.resolve_segment_preprocessed(
            segment
        )
        source_path = self.service.resolve_segment_source(
            segment
        )

        if (
            rectified_path is not None
            and rectified_path.is_file()
        ):
            try:
                self.viewer.set_working_image(
                    rectified_path
                )
            except ValueError as exc:
                logger.exception(
                    "Could not load rectified segment image %s",
                    rectified_path,
                )
                self.viewer.clear_image()
                self.live_enhancement.clear_base_image()
                self.preprocess_panel.set_image_available(
                    False
                )
                self.calibration_panel.set_image_available(False)
                self._active_depth_calibration = None
                self._show_error(
                    "Cannot display rectified image",
                    exc,
                )
                return

            segment.source_missing = False

            self.preprocess_panel.set_image_available(
                True
            )

            preprocess = segment.extensions.get(
                "preprocess",
                {},
            )
            enhancement_settings = (
                self.service
                .get_enhancement_settings(
                    segment
                )
            )

            # Reproduce the visible working image from the rectified base plus
            # persisted settings. The saved working PNG is a downstream artifact,
            # not the editable preprocessing base.
            self.live_enhancement.clear_base_image()
            self.live_enhancement.set_settings(
                enhancement_settings
            )
            self.live_enhancement.set_base_image(
                self.viewer.source_image_array(),
                reset_settings=False,
            )

            self.preprocess_panel.set_enhancement_settings(
                enhancement_settings
            )

            self.preprocess_panel.enter_enhancement_stage(
                preprocess.get(
                    "derived_path",
                    rectified_path.name,
                ),
                preprocess.get(
                    "working_path"
                ),
            )
            self._refresh_depth_calibration()
            self.curve_calibration_controller.refresh()

            return

        if (
            source_path is None
            or not source_path.is_file()
        ):
            segment.source_missing = True
            self.viewer.clear_image()
            self.live_enhancement.clear_base_image()

            self.preprocess_panel.set_image_available(
                False
            )
            self._refresh_depth_calibration()
            self.curve_calibration_controller.refresh()

            self.statusBar().showMessage(
                f"Missing source image: {segment.source_path}",
                7000,
            )

            self._refresh_project_panel()
            return

        try:
            self.viewer.load_image(
                source_path
            )
        except ValueError as exc:
            logger.exception(
                "Could not load segment image %s",
                source_path,
            )

            self.viewer.clear_image()
            self.live_enhancement.clear_base_image()

            self.preprocess_panel.set_image_available(
                False
            )
            self._refresh_depth_calibration()
            self.curve_calibration_controller.refresh()

            self._show_error(
                "Cannot display image",
                exc,
            )
            return

        segment.source_missing = False

        self.preprocess_panel.set_image_available(
            True
        )

        self.live_enhancement.clear_base_image()
        self.preprocess_panel.enter_rectification_stage()
        self._refresh_depth_calibration()
        self.curve_calibration_controller.refresh()

        preprocess = segment.extensions.get(
            "preprocess",
            {},
        )

        stored_points = preprocess.get(
            "corner_points"
        )
        stored_pairs = preprocess.get(
            "control_pairs"
        )
        stored_mode = preprocess.get(
            "mode"
        )

        if stored_mode == "multipoint_pairs" and stored_pairs:
            try:
                self.viewer.set_rectification_control_pairs(
                    stored_pairs
                )
                self.preprocess_panel.multipoint_check.setChecked(True)
            except (
                TypeError,
                ValueError,
            ) as exc:
                logger.warning(
                    "Could not restore multipoint rectification controls for %s: %s",
                    segment.segment_id,
                    exc,
                )
                if stored_points:
                    try:
                        self.viewer.set_rectification_points(stored_points)
                    except (TypeError, ValueError) as corner_exc:
                        logger.warning(
                            "Could not restore rectification points for %s: %s",
                            segment.segment_id,
                            corner_exc,
                        )
        elif stored_points:
            try:
                self.viewer.set_rectification_points(
                    stored_points
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                logger.warning(
                    "Could not restore rectification points for %s: %s",
                    segment.segment_id,
                    exc,
                )

    def _rectification_points_changed(
        self,
        points,
    ) -> None:
        self.preprocess_panel.set_selection_state(
            point_count=len(points),
            is_valid=(
                self.viewer
                .rectification
                .is_geometry_valid
            ),
            error=(
                self.viewer
                .rectification
                .validation_error
            ),
            estimated_size=(
                self.viewer
                .rectification
                .estimated_size
            ),
        )

    def _rectification_validation_changed(
        self,
        is_valid: bool,
        error,
        estimated_size,
    ) -> None:
        self.preprocess_panel.set_selection_state(
            point_count=len(
                self.viewer.rectification_points
            ),
            is_valid=is_valid,
            error=error,
            estimated_size=estimated_size,
        )

    def _rectification_control_pairs_changed(
        self,
        pairs,
    ) -> None:
        self.preprocess_panel.set_multipoint_state(
            control_pair_count=len(pairs),
            is_valid=(
                self.viewer.rectification.multipoint_is_geometry_valid
            ),
            error=(
                self.viewer.rectification.multipoint_validation_error
            ),
        )

    def _rectification_multipoint_validation_changed(
        self,
        is_valid: bool,
        error,
    ) -> None:
        self.preprocess_panel.set_multipoint_state(
            control_pair_count=len(
                self.viewer.rectification_extra_control_pairs
            ),
            is_valid=is_valid,
            error=error,
        )

    def _record_current_project_as_recent(self) -> None:
        """Remember the current project after a successful disk save."""
        if (
            self.service.project is None
            or self.service.paths is None
        ):
            return
        self.recent_projects.record_saved_project(
            self.service.paths.project_file,
            self.service.project.name,
        )

    @staticmethod
    def _recent_saved_text(saved_at_ns: int) -> str:
        try:
            timestamp = datetime.fromtimestamp(saved_at_ns / 1_000_000_000)
        except (OSError, OverflowError, ValueError):
            return "recently"
        return timestamp.strftime("%d/%m/%Y %H:%M")

    def _refresh_recent_projects_ui(self) -> None:
        """Rebuild menu and start-page controls from one QSettings history."""
        entries = self.recent_projects.entries()

        if hasattr(self, "recent_projects_menu"):
            self.recent_projects_menu.clear()
            if not entries:
                action = self.recent_projects_menu.addAction(
                    "No recent projects"
                )
                action.setEnabled(False)
            else:
                for index, entry in enumerate(entries, start=1):
                    action = self.recent_projects_menu.addAction(
                        f"{index}. {entry.name}"
                    )
                    action.setStatusTip(entry.path)
                    action.triggered.connect(
                        lambda _checked=False, path=entry.path: (
                            self._open_recent_project(Path(path))
                        )
                    )

        while self.welcome_recent_layout.count():
            item = self.welcome_recent_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._welcome_recent_buttons.clear()

        if not entries:
            empty = QLabel("No recently saved projects yet.")
            empty.setStyleSheet("color: #6B7280; padding: 6px 2px;")
            self.welcome_recent_layout.addWidget(empty)
            return

        for entry in entries:
            button = QPushButton(
                f"{entry.name}\n{entry.path}\n"
                f"Saved {self._recent_saved_text(entry.saved_at_ns)}"
            )
            button.setToolTip(f"Open {entry.path}")
            button.setMinimumHeight(68)
            button.setStyleSheet(
                "QPushButton { text-align: left; padding: 8px 12px; }"
            )
            button.clicked.connect(
                lambda _checked=False, path=entry.path: (
                    self._open_recent_project(Path(path))
                )
            )
            self.welcome_recent_layout.addWidget(button)
            self._welcome_recent_buttons.append(button)

    def _refresh_central_page(self) -> None:
        """Show the welcome page only while no project is open."""
        self.central_stack.setCurrentWidget(
            self.viewer if self.service.has_project else self.welcome_page
        )

    def _refresh_all(self) -> None:
        self._refresh_project_panel()
        self._refresh_title()
        self._refresh_recent_projects_ui()
        self._refresh_central_page()

        enabled = self.service.has_project
        self.calibration_panel.set_project_available(enabled)
        self.save_action.setEnabled(
            enabled
        )
        self.add_action.setEnabled(
            enabled
        )
        self._refresh_export_action()

        if not enabled:
            self.preprocess_panel.set_image_available(
                False
            )
            self.calibration_panel.set_image_available(False)
            self._active_depth_calibration = None

        self.curve_calibration_controller.refresh()
        self.manual_digitization_controller.refresh()
        self._refresh_image_overlap_guides()
        self._update_navigation_hud_from_center()

    def _refresh_project_panel(self) -> None:
        self.project_panel.set_project(
            self.service.project,
            self.service.active_segment_id,
            self.service.get_project_workflow_summaries(),
            self.service.get_project_depth_intervals(),
        )

    def _sync_project_explorer_to_digitization_curve(
        self,
        curve_id: str,
    ) -> None:
        """Focus the active curve's Digitization branch in Project Explorer."""
        segment_id = self.service.active_segment_id
        if not segment_id or not curve_id:
            return
        self.project_panel.focus_digitization_curve(
            segment_id,
            curve_id,
        )

    def _sync_project_explorer_to_calibration_curve(
        self,
        curve_id: str,
    ) -> None:
        """Focus the active curve's X-calibration branch in Project Explorer."""
        segment_id = self.service.active_segment_id
        if not segment_id or not curve_id:
            return
        self.project_panel.focus_calibration_curve(
            segment_id,
            curve_id,
        )

    def _refresh_project_state(self) -> None:
        """Refresh title plus the derived workflow tree after persisted edits."""
        self._refresh_title()
        self._refresh_project_panel()
        self._refresh_image_overlap_guides()
        self._refresh_export_action()
        self._update_navigation_hud_from_center()

    def _refresh_export_action(self) -> None:
        available = self.service.has_project and self.service.can_export_las()
        self.quality_control_action.setEnabled(available)
        self.las_curve_preview_action.setEnabled(available)
        self.export_las_action.setEnabled(available)

    def _refresh_image_overlap_guides(self) -> None:
        """Show overlap guides only for the currently active project curve.

        Each curve has its own implicit image sequence, defined by the existing
        "Curve present in this image" flag. Parallel images that contain other
        curves therefore cannot create unrelated previous/next guides.
        """
        self.viewer.clear_image_overlap_guides()
        segment = self.service.active_segment
        if (
            segment is None
            or not self.viewer.has_image
        ):
            return

        curve_id = self._hud_active_curve_id()
        if not curve_id:
            return

        calibration = self.service.get_depth_calibration(segment)
        if calibration is None:
            return
        unit = self._depth_unit_text(calibration.unit)
        definitions: list[ImageOverlapGuideDefinition] = []
        for guide in self.service.get_image_overlap_guides(
            segment,
            curve_id,
        ):
            try:
                y_px = depth_to_pixel_y(guide.depth, calibration)
            except (CalibrationError, CalibrationRangeError):
                continue

            if guide.relation is ImageOverlapRelation.PREVIOUS:
                relation = "previous"
                label_above = calibration.depth_increases_with_y
            else:
                relation = "next"
                label_above = not calibration.depth_increases_with_y
            direction = "above" if label_above else "below"
            definitions.append(
                ImageOverlapGuideDefinition(
                    y_px=y_px,
                    text=(
                        f"Overlap with {relation} image — digitize slightly "
                        f"{direction} this line."
                    ),
                    label_above=label_above,
                    tooltip=(
                        f"{guide.neighbour_segment_name} · Shared overlap reference: "
                        f"{guide.depth:g} {unit}"
                    ),
                )
            )
        self.viewer.set_image_overlap_guides(definitions)

    def _project_tree_node_activated(
        self,
        segment_id: str,
        node_type: str,
        curve_id: str,
    ) -> None:
        """Navigate from one workflow-tree node to the corresponding workspace."""
        if self.service.active_segment_id != segment_id:
            self.select_segment(segment_id)
            if self.service.active_segment_id != segment_id:
                return

        if curve_id:
            # Both right-hand panels keep the same project-curve selection so
            # X calibration and digitization stay synchronized.
            self.curve_calibration_controller.refresh(
                selected_curve_id=curve_id
            )
            self.manual_digitization_controller.refresh(
                selected_curve_id=curve_id
            )

        if node_type == self.project_panel.NODE_PREPROCESSING:
            self.preprocessing_dock.raise_()
        elif node_type in (
            self.project_panel.NODE_DEPTH,
            self.project_panel.NODE_X,
        ):
            self.calibration_dock.raise_()
        elif node_type == self.project_panel.NODE_DIGITIZATION:
            self.digitization_dock.raise_()
        # A curve parent selects the curve in both panels but deliberately does
        # not force a tab change.

    def _refresh_title(self) -> None:
        if self.service.project:
            dirty = (
                " *"
                if self.service.dirty
                else ""
            )
            self.setWindowTitle(
                f"{self.service.project.name} "
                f"— LogDigitizer{dirty}"
            )
        else:
            self.setWindowTitle(
                "LogDigitizer"
            )

    def _update_coordinates(
        self,
        x: float,
        y: float,
        valid: bool,
    ) -> None:
        if not valid:
            self.curve_calibration_controller.update_cursor(0.0, False)
            self.x_status.setText(
                "X: —"
            )
            self.y_status.setText(
                "Y: —"
            )
            self.depth_status.setText(
                "Depth: —"
            )
            # Leaving the raster restores the navigation-oriented center HUD.
            self._update_navigation_hud_from_center()
            return

        self.x_status.setText(
            f"X: {x:.1f}"
        )
        self.y_status.setText(
            f"Y: {y:.1f}"
        )
        curve_id = self.curve_calibration_controller.active_curve_id
        scale_id, scale_offset = self._cursor_scale_settings(curve_id, y)
        self.curve_calibration_controller.update_cursor(
            x,
            True,
            scale_id=scale_id,
            scale_offset=scale_offset,
        )
        self._update_navigation_hud_at_position(x, y)

        calibration = self._active_depth_calibration
        if calibration is None:
            self.depth_status.setText(
                "Depth: Not calibrated"
            )
            return

        try:
            depth = pixel_y_to_depth(
                y,
                calibration,
            )
        except CalibrationRangeError:
            self.depth_status.setText(
                "Depth: Outside valid range"
            )
            return

        self.depth_status.setText(
            f"Depth: {depth:.2f} "
            f"{self._depth_unit_text(calibration.unit)}"
        )

    def _update_navigation_hud_from_center_signal(
        self,
        x: float,
        y: float,
        valid: bool,
    ) -> None:
        """Switch the HUD to viewport-center values after navigation."""
        if not valid:
            self.viewer.clear_navigation_hud()
            return
        self._update_navigation_hud_at_position(x, y)

    def _update_navigation_hud_from_center(self) -> None:
        """Refresh the HUD from the visual center without changing status-bar data."""
        point = self.viewer.visible_center_image_point()
        if point is None:
            self.viewer.clear_navigation_hud()
            return
        self._update_navigation_hud_at_position(
            float(point.x()),
            float(point.y()),
        )

    def _set_hud_scale_context_source(self, source: str) -> None:
        """Remember which workspace defines HUD and overlap-curve context."""
        normalized = str(source).strip().lower()
        if normalized not in {"calibration", "digitization"}:
            return
        self._hud_scale_context_source = normalized
        self._refresh_image_overlap_guides()
        self._update_navigation_hud_from_center()

    def _update_navigation_hud_at_position(
        self,
        x: float,
        y: float,
    ) -> None:
        """Render depth, working curve value, scale/offset, and mismatch QC."""
        if not self.viewer.has_image:
            self.viewer.clear_navigation_hud()
            return

        calibration = self._active_depth_calibration
        if calibration is None:
            depth_text = "—"
        else:
            try:
                depth = pixel_y_to_depth(
                    y,
                    calibration,
                )
            except CalibrationRangeError:
                depth_text = "—"
            else:
                depth_text = (
                    f"{depth:.1f} "
                    f"{self._depth_unit_text(calibration.unit)}"
                )

        curve_id = self._hud_active_curve_id()
        working_scale_id, working_offset = self._hud_working_scale_settings(
            curve_id
        )

        # The HUD readout follows the scale/offset that was SAVED for the
        # digitized section at the current Y. This keeps the numerical value
        # faithful to the existing trace when the curve changes scale or wraps
        # with a linear offset. Only when there is no saved section at this Y
        # do we fall back to the scale/offset currently selected for new work.
        saved_settings = (
            self._saved_scale_settings_at_y(curve_id, y)
            if curve_id is not None
            else None
        )
        display_scale_id, display_offset = (
            saved_settings
            if saved_settings is not None
            else (working_scale_id, working_offset)
        )

        readout = self.curve_calibration_controller.cursor_readout(
            x,
            scale_id=display_scale_id,
            scale_offset=display_offset,
            curve_id=curve_id,
        )
        curve = readout.curve
        if curve is None:
            self.viewer.set_navigation_hud(
                depth_text=depth_text,
            )
            return

        value_text = "—"
        if readout.status == "ok" and readout.value is not None:
            value_text = f"{readout.value:.1f}"
            if curve.unit:
                value_text += f" {curve.unit}"

        scale_text = self._format_hud_scale_context(
            curve.curve_id,
            display_scale_id,
            display_offset,
        )
        warning_text = ""
        if saved_settings is not None and saved_settings != (
            working_scale_id,
            working_offset,
        ):
            warning_text = (
                "Active setting: "
                + self._format_hud_scale_context(
                    curve.curve_id,
                    working_scale_id,
                    working_offset,
                )
            )

        self.viewer.set_navigation_hud(
            depth_text=depth_text,
            curve_mnemonic=curve.mnemonic,
            curve_value_text=value_text,
            mnemonic_color=curve.display_color,
            scale_text=scale_text,
            warning_text=warning_text,
        )

    def _hud_active_curve_id(self) -> str | None:
        """Return the curve whose working context should be shown in the HUD."""
        manual = self.manual_digitization_controller
        if manual.tracing and manual.active_curve_id:
            return manual.active_curve_id

        automatic = self.automatic_digitization_controller
        if automatic.active and automatic.active_curve_id:
            return automatic.active_curve_id

        if self._hud_scale_context_source == "digitization":
            if self.digitization_panel.selected_curve_id:
                return self.digitization_panel.selected_curve_id

        if self._hud_scale_context_source == "calibration":
            if self.calibration_panel.selected_curve_id:
                return self.calibration_panel.selected_curve_id

        return (
            self.curve_calibration_controller.active_curve_id
            or self.digitization_panel.selected_curve_id
            or self.calibration_panel.selected_curve_id
        )

    def _hud_working_scale_settings(
        self,
        curve_id: str | None,
    ) -> tuple[str, int]:
        """Return the scale/offset that a new trace would use right now."""
        if curve_id is None:
            return DEFAULT_HORIZONTAL_SCALE_ID, 0

        manual = self.manual_digitization_controller
        if manual.tracing and manual.active_curve_id == curve_id:
            return (
                manual.active_scale_id or DEFAULT_HORIZONTAL_SCALE_ID,
                manual.active_scale_offset or 0,
            )

        automatic = self.automatic_digitization_controller
        if automatic.active and automatic.active_curve_id == curve_id:
            return (
                automatic.active_scale_id or DEFAULT_HORIZONTAL_SCALE_ID,
                automatic.active_scale_offset or 0,
            )

        if (
            self._hud_scale_context_source == "calibration"
            and self.calibration_panel.selected_curve_id == curve_id
        ):
            return self.calibration_panel.selected_scale_id, 0

        if (
            self._hud_scale_context_source == "digitization"
            and self.digitization_panel.selected_curve_id == curve_id
        ):
            return (
                self.digitization_panel.selected_scale_id,
                self.digitization_panel.scale_offset,
            )

        # Fallback for programmatic refreshes where no user interaction has yet
        # established the right-hand workspace source.
        if self.digitization_panel.selected_curve_id == curve_id:
            return (
                self.digitization_panel.selected_scale_id,
                self.digitization_panel.scale_offset,
            )
        if self.calibration_panel.selected_curve_id == curve_id:
            return self.calibration_panel.selected_scale_id, 0
        return DEFAULT_HORIZONTAL_SCALE_ID, 0

    def _saved_scale_settings_at_y(
        self,
        curve_id: str,
        y_px: float,
    ) -> tuple[str, int] | None:
        """Return saved trace settings at Y solely for HUD mismatch feedback."""
        try:
            digitized = self.service.get_digitized_curve(curve_id)
        except (KeyError, RuntimeError):
            return None
        if digitized is None:
            return None
        return digitized.scale_settings_at_y(y_px)

    def _format_hud_scale_context(
        self,
        curve_id: str,
        scale_id: str,
        scale_offset: int,
    ) -> str:
        """Return a compact human label such as ``0–100 API · +1 offset``."""
        try:
            curve = self.service.get_curve(curve_id)
            profile = self.service.get_horizontal_scale_profile(
                curve_id,
                scale_id,
            )
        except (KeyError, RuntimeError):
            return "—"

        if profile is None:
            label = "—"
        else:
            full_name = profile.display_name(curve)
            prefix = f"{curve.mnemonic}: "
            label = (
                full_name[len(prefix):]
                if full_name.startswith(prefix)
                else full_name
            )

        offset = int(scale_offset)
        if offset:
            label += f" · {offset:+d} offset"
        return label

    def _cursor_scale_offset(
        self,
        curve_id: str | None,
        y_px: float,
    ) -> int:
        """Compatibility wrapper returning only the contextual wrap offset."""
        return self._cursor_scale_settings(curve_id, y_px)[1]

    def _cursor_scale_settings(
        self,
        curve_id: str | None,
        y_px: float,
    ) -> tuple[str, int]:
        """Resolve the calibration profile and wrap offset at the cursor."""
        if curve_id is None:
            return DEFAULT_HORIZONTAL_SCALE_ID, 0

        manual = self.manual_digitization_controller
        if manual.tracing and manual.active_curve_id == curve_id:
            return (
                manual.active_scale_id or DEFAULT_HORIZONTAL_SCALE_ID,
                manual.active_scale_offset or 0,
            )

        automatic = self.automatic_digitization_controller
        if automatic.active and automatic.active_curve_id == curve_id:
            return (
                automatic.active_scale_id or DEFAULT_HORIZONTAL_SCALE_ID,
                automatic.active_scale_offset or 0,
            )

        try:
            digitized = self.service.get_digitized_curve(curve_id)
        except (KeyError, RuntimeError):
            digitized = None
        if digitized is not None:
            saved_settings = digitized.scale_settings_at_y(y_px)
            if saved_settings is not None:
                return saved_settings

        if self.digitization_panel.selected_curve_id == curve_id:
            return (
                self.digitization_panel.selected_scale_id,
                self.digitization_panel.scale_offset,
            )
        return DEFAULT_HORIZONTAL_SCALE_ID, 0

    def _update_zoom(
        self,
        factor: float,
    ) -> None:
        self.zoom_status.setText(
            f"Zoom: {factor * 100:.1f}%"
        )

    def _confirm_unsaved_changes(self) -> bool:
        # Capture marker UI state before a close/open/new decision. This also
        # makes legacy projects persist their currently visible positions the
        # next time they are saved.
        self._persist_visible_calibration_marker_positions()
        if not self.service.dirty:
            return True

        response = QMessageBox.warning(
            self,
            "Unsaved changes",
            "The current project has unsaved changes.",
            (
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            ),
            QMessageBox.StandardButton.Save,
        )

        if (
            response
            == QMessageBox.StandardButton.Cancel
        ):
            return False

        if (
            response
            == QMessageBox.StandardButton.Save
        ):
            self.save_project()
            return not self.service.dirty

        return True

    def closeEvent(  # noqa: N802 - Qt API
        self,
        event: QCloseEvent,
    ) -> None:
        if self._confirm_unsaved_changes():
            event.accept()
        else:
            event.ignore()

    def open_user_guide(self) -> None:
        guide_path = user_guide_path()
        if not guide_path.is_file():
            QMessageBox.warning(
                self,
                "User Guide not found",
                (
                    "The LogDigitizer User Guide could not be found.\n\n"
                    f"Expected location:\n{guide_path}"
                ),
            )
            return

        if not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(guide_path))
        ):
            QMessageBox.warning(
                self,
                "Cannot open User Guide",
                (
                    "The LogDigitizer User Guide could not be opened. "
                    "Check that a PDF viewer is installed."
                ),
            )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About LogDigitizer",
            (
                "<b>LogDigitizer</b><br>"
                f"Development version {__version__}<br><br>"
                "Scientific desktop software for digitizing "
                "scanned historical logs.<br>"
                "Initial author: Carlos Marín Lechado<br>"
                "Initially linked to IGME-CSIC.<br><br>"
                "License and scientific citation are not yet finalized."
            ),
        )

    def _show_error(
        self,
        title: str,
        exc: Exception,
    ) -> None:
        logger.log(
            logging.ERROR,
            "%s: %s",
            title,
            exc,
        )
        QMessageBox.critical(
            self,
            title,
            str(exc),
        )
