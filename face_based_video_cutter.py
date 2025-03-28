import sys
import os
import cv2
import numpy as np
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                            QPushButton, QLabel, QFileDialog, QScrollArea, 
                            QGridLayout, QCheckBox, QMessageBox, QTextEdit,
                            QSlider, QGroupBox, QFormLayout, QComboBox)
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import Qt, QThread, pyqtSignal
import face_recognition

class FaceDetectionThread(QThread):
    progress_update = pyqtSignal(int)
    detection_finished = pyqtSignal(list, list, list, list, int)

    def __init__(self, video_path, face_tolerance=0.7, sample_interval=1):
        super().__init__()
        self.video_path = video_path
        self.face_tolerance = face_tolerance
        self.sample_interval = sample_interval
        self.running = True
        
    def run(self):
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            return
            
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        all_faces = []
        face_encodings = []
        face_frames = []
        unique_faces = []
        
        frame_count = 0
        processed_frames = 0
        while cap.isOpened() and self.running:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_count % self.sample_interval == 0:
                small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                
                face_locations = face_recognition.face_locations(rgb_small_frame)
                encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
            
                for (top, right, bottom, left), encoding in zip(face_locations, encodings):
                    top *= 4
                    right *= 4
                    bottom *= 4
                    left *= 4
                    
                    face_img = frame[top:bottom, left:right]
                    if face_img.size > 0:
                        all_faces.append(face_img)
                        face_encodings.append(encoding)
                        face_frames.append(frame_count)
                        
                        if not unique_faces:
                            unique_faces.append({
                                'encoding': encoding,
                                'face_img': face_img,
                                'indices': [len(all_faces) - 1]
                            })
                        else:
                            matched = False
                            for person in unique_faces:
                                if face_recognition.compare_faces([person['encoding']], encoding, tolerance=self.face_tolerance)[0]:
                                    person['indices'].append(len(all_faces) - 1)
                                    matched = True
                                    break
                            
                            if not matched:
                                unique_faces.append({
                                    'encoding': encoding,
                                    'face_img': face_img,
                                    'indices': [len(all_faces) - 1]
                                })

                processed_frames += 1
            
            frame_count += 1

            progress = int((frame_count / total_frames) * 100)
            self.progress_update.emit(progress)
            
        cap.release()
        
        if self.running:
            self.detection_finished.emit(all_faces, face_encodings, face_frames, unique_faces, total_frames)
    
    def stop(self):
        self.running = False


class VideoWriterThread(QThread):
    progress_update = pyqtSignal(int)
    cutting_finished = pyqtSignal(str)
    
    def __init__(self, video_path, output_path, selected_frames, codec, sample_interval=1):
        super().__init__()
        self.video_path = video_path
        self.output_path = output_path
        self.selected_frames = selected_frames
        self.codec = codec
        self.sample_interval = sample_interval  # Novo parâmetro
        self.running = True
        
    def run(self):
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            return
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        fourcc = cv2.VideoWriter_fourcc(*self.codec)
        out = cv2.VideoWriter(self.output_path, fourcc, fps, (width, height), isColor=True)
        
        selected_frame_ranges = []
        current_range_start = None
        last_selected_frame = None

        sorted_frames = sorted(self.selected_frames)
        
        for frame in sorted_frames:
            if current_range_start is None:
                current_range_start = frame
                last_selected_frame = frame
            elif frame - last_selected_frame > self.sample_interval:
                selected_frame_ranges.append((current_range_start, last_selected_frame))
                current_range_start = frame
            
            last_selected_frame = frame

        if current_range_start is not None:
            selected_frame_ranges.append((current_range_start, last_selected_frame))

        if not out.isOpened():
            self.cutting_finished.emit("Failed")
            return
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_count = 0
        
        while cap.isOpened() and self.running:
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_count in self.selected_frames:
                out.write(frame)
            
            frame_count += 1
            progress = int((frame_count / total_frames) * 100)
            self.progress_update.emit(progress)
        
        cap.release()
        out.release()
        
        if self.running:
            self.cutting_finished.emit(self.codec)
    
    def stop(self):
        self.running = False


class FaceBasedVideoCutter(QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("Face-Based Video Cutter")
        self.setGeometry(100, 100, 800, 800)
        
        self.video_path = None
        self.all_faces = []
        self.face_encodings = []
        self.face_frames = []
        self.unique_faces = []
        self.selected_persons = set()
        self.face_tolerance = 0.7
        
        self.detection_thread = None
        self.writer_thread = None
        
        self.setup_ui()
        
    def setup_ui(self):
        main_layout = QVBoxLayout()
        
        self.load_button = QPushButton("Load Video")
        self.load_button.clicked.connect(self.load_video)
        main_layout.addWidget(self.load_button)
        
        self.video_info = QLabel("(No video loaded)")
        main_layout.addWidget(self.video_info)
        
        settings_group = QGroupBox("Detection Settings")
        settings_layout = QFormLayout()

        tolerance_container = QVBoxLayout()
        self.tolerance_label = QLabel(f"Similarity Tolerance: {self.face_tolerance:.1f}")
        tolerance_container.addWidget(self.tolerance_label)
        
        self.tolerance_slider = QSlider(Qt.Orientation.Horizontal)
        self.tolerance_slider.setRange(1, 10)
        self.tolerance_slider.setValue(int(self.face_tolerance * 10))
        self.tolerance_slider.setTickInterval(1)
        self.tolerance_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.tolerance_slider.valueChanged.connect(self.update_tolerance)
        tolerance_container.addWidget(self.tolerance_slider)

        tolerance_description = QLabel("Lower = More differentiation between people | Higher = More similar people grouped")
        tolerance_description.setWordWrap(True)
        tolerance_container.addWidget(tolerance_description)

        settings_layout.addRow(tolerance_container)
        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)

        self.sample_interval_label = QLabel("Frame Sampling Interval: 1")
        settings_layout.addRow(self.sample_interval_label)

        self.sample_interval_slider = QSlider(Qt.Orientation.Horizontal)
        self.sample_interval_slider.setRange(1, 10)
        self.sample_interval_slider.setValue(1)
        self.sample_interval_slider.setTickInterval(1)
        self.sample_interval_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.sample_interval_slider.valueChanged.connect(self.update_sample_interval)
        settings_layout.addRow(self.sample_interval_slider)

        sample_description = QLabel("1 = All frames | 10 = Only 1 in every 10 frames")
        sample_description.setWordWrap(True)
        settings_layout.addRow(sample_description)
        
        control_layout = QHBoxLayout()
        self.detect_button = QPushButton("Detect Faces and People")
        self.detect_button.clicked.connect(self.detect_faces)
        self.detect_button.setEnabled(False)
        control_layout.addWidget(self.detect_button)
        
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.setEnabled(False)
        control_layout.addWidget(self.cancel_button)
        main_layout.addLayout(control_layout)
        
        self.status_log = QTextEdit()
        self.status_log.setReadOnly(True)
        self.status_log.setMinimumHeight(80)
        self.status_log.setMaximumHeight(120)
        self.status_log.append("Status: Waiting for video...")
        main_layout.addWidget(self.status_log)

        self.clear_log_button = QPushButton("Clear Logs")
        self.clear_log_button.clicked.connect(lambda: self.status_log.clear())
        main_layout.addWidget(self.clear_log_button)
        
        self.detection_info = QLabel("Total Frames: 0 | Frames with Faces: 0 | Total Faces: 0 | People: 0")
        main_layout.addWidget(self.detection_info)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QGridLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(self.scroll_area)
        
        export_group = QGroupBox("Export Settings")
        export_layout = QFormLayout()

        self.codec_combo = QComboBox()
        self.codec_combo.addItem("H.264 (Recommended for MP4)", "avc1")
        self.codec_combo.addItem("XVID (Recommended for AVI)", "XVID")
        self.codec_combo.addItem("MPEG-4 (Recommended for MOV)", "mp4v")
        self.codec_combo.addItem("MJPG (High quality, large files)", "MJPG")
        self.codec_combo.addItem("FFV1 (Lossless, very large files)", "FFV1")
        export_layout.addRow("Codec:", self.codec_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems([".mp4", ".avi", ".mov", ".mkv"])
        export_layout.addRow("Format:", self.format_combo)

        export_group.setLayout(export_layout)
        main_layout.addWidget(export_group)
        
        self.cut_button = QPushButton("Process and Export")
        self.cut_button.clicked.connect(self.cut_video)
        self.cut_button.setEnabled(False)
        main_layout.addWidget(self.cut_button)
        
        self.setLayout(main_layout)
    
    def update_tolerance(self, value):
        self.face_tolerance = value / 10.0
        self.tolerance_label.setText(f"Similarity Tolerance: {self.face_tolerance:.1f}")

    def update_sample_interval(self, value):
        self.sample_interval_label.setText(f"Intervalo de Amostragem de Quadros: {value}")

    def load_video(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "", "Videos (*.mp4 *.avi *.mov *.mkv)"
        )
        
        if not file_path:
            return
            
        self.set_buttons_state(False)
        
        self.video_path = file_path
        
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            QMessageBox.critical(self, "Error", "Could not open the video.")
            self.log_status("Error loading video.")
            self.set_buttons_state(True)
            return
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        cap.release()
        
        self.video_info.setText(f"Name: {os.path.basename(file_path)} | "
                              f"Duration: {duration:.1f}s | "
                              f"Resolution: {width}x{height} | "
                              f"FPS: {fps:.2f}")
        self.detection_info.setText(f"Total Frames: {frame_count} | Frames with Faces: 0 | Total Faces: 0 | People: 0")
        self.log_status("Video loaded. Waiting for person detection.")
        
        self.set_buttons_state(True)
        self.detect_button.setEnabled(True)
        self.cut_button.setEnabled(False)
        
        self.all_faces = []
        self.face_encodings = []
        self.face_frames = []
        self.unique_faces = []
        self.selected_persons.clear()
        
        self.clear_face_display()
    
    def clear_face_display(self):
        for i in reversed(range(self.scroll_layout.count())):
            self.scroll_layout.itemAt(i).widget().setParent(None)
    
    def detect_faces(self):
        if not self.video_path:
            return
        
        self.set_buttons_state(False)
        self.cancel_button.setEnabled(True)

        sample_interval = self.sample_interval_slider.value()
        
        self.log_status(f"Starting face detection with tolerance {self.face_tolerance:.1f}...")
        self.clear_face_display()
        
        self.detection_thread = FaceDetectionThread(
            self.video_path, 
            face_tolerance=self.face_tolerance,
            sample_interval=sample_interval
        )
        self.detection_thread.progress_update.connect(self.update_detection_progress)
        self.detection_thread.detection_finished.connect(self.process_detection_results)
        self.detection_thread.start()
    
    def update_detection_progress(self, value):
        self.log_status(f"Analyzing video... {value}%")
    
    def process_detection_results(self, all_faces, face_encodings, face_frames, unique_faces, total_frames):
        self.all_faces = all_faces
        self.face_encodings = face_encodings
        self.face_frames = face_frames
        self.unique_faces = unique_faces
        
        self.set_buttons_state(True)
        self.cancel_button.setEnabled(False)
        self.cut_button.setEnabled(False)
        
        self.log_status(f"{len(unique_faces)} people found with tolerance {self.face_tolerance:.1f}! Select one or more:")
        self.detection_info.setText(f"Total Frames: {total_frames} | "
                                   f"Frames with Faces: {len(set(self.face_frames))} | "
                                   f"Total Faces: {len(self.all_faces)} | "
                                   f"People: {len(self.unique_faces)}")
        
        self.display_unique_faces()
    
    def display_unique_faces(self):
        self.clear_face_display()
        
        cols = 4
        for i, person in enumerate(self.unique_faces):
            row = i // cols
            col = i % cols
            
            person_widget = QWidget()
            person_layout = QVBoxLayout()
            
            face_img = cv2.cvtColor(person['face_img'], cv2.COLOR_BGR2RGB)
            h, w, ch = face_img.shape
            bytes_per_line = ch * w
            q_image = QImage(face_img.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            
            pixmap = QPixmap.fromImage(q_image).scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio)
            
            img_label = QLabel()
            img_label.setPixmap(pixmap)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            checkbox = QCheckBox(f"Person {i+1} ({len(person['indices'])} appearances)")
            checkbox.stateChanged.connect(lambda state, idx=i: self.update_selection(idx, state))
            
            person_layout.addWidget(img_label)
            person_layout.addWidget(checkbox)
            person_widget.setLayout(person_layout)
            
            self.scroll_layout.addWidget(person_widget, row, col)
    
    def update_selection(self, index, state):
        if state:
            self.selected_persons.add(index)
        else:
            self.selected_persons.discard(index)
        
        self.log_status(f"{len(self.selected_persons)} people selected")
        
        self.cut_button.setEnabled(len(self.selected_persons) > 0)

    def validate_codec_format_compatibility(self, codec, format):
        incompatibilities = {
            'FFV1': {
                '.mp4': "FFV1 codec is not supported in MP4 container. Use MKV format.",
                '.avi': "FFV1 codec works best with MKV format.",
                '.mov': "FFV1 codec works best with MKV format."
            },
            'avc1': {
                '.mkv': "H.264 (avc1) codec might have compatibility issues with MKV.",
            },
            'XVID': {
                '.mp4': "XVID codec is recommended for AVI, not MP4.",
                '.mov': "XVID codec is recommended for AVI, not MOV."
            },
            'mp4v': {
                '.avi': "MPEG-4 codec might have issues with AVI format.",
                '.mkv': "MPEG-4 codec is not ideal for MKV."
            }
        }

        if codec in incompatibilities and format in incompatibilities[codec]:
            return False, incompatibilities[codec][format]
        
        return True, ""
    
    def cut_video(self):
        if not self.video_path or not self.selected_persons:
            QMessageBox.warning(self, "Warning", "Select at least one person to cut the video.")
            return
        
        selected_codec = self.codec_combo.currentData()
        selected_format = self.format_combo.currentText()

        is_compatible, error_message = self.validate_codec_format_compatibility(selected_codec, selected_format)
    
        if not is_compatible:
            response = QMessageBox.warning(
                self, 
                "Codec-Format Compatibility", 
                f"{error_message}\n\nDo you want to continue anyway?", 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if response == QMessageBox.StandardButton.No:
                return

        output_path, _ = QFileDialog.getSaveFileName(
            self, "Save Video", "", f"Videos (*{selected_format})"
        )
        
        if not output_path:
            return
        
        if not output_path.endswith(selected_format):
            output_path += selected_format
        
        self.set_buttons_state(False)
        
        selected_indices = set()
        for person_idx in self.selected_persons:
            selected_indices.update(self.unique_faces[person_idx]['indices'])
        
        selected_frames = set([self.face_frames[idx] for idx in selected_indices])
        
        selected_codec = self.codec_combo.currentData()
        sample_interval = self.sample_interval_slider.value()
        
        self.log_status("Starting video processing and export...")
        self.writer_thread = VideoWriterThread(self.video_path, output_path, selected_frames, selected_codec, sample_interval)
        self.writer_thread.progress_update.connect(self.update_cutting_progress)
        self.writer_thread.cutting_finished.connect(self.finish_cutting)
        self.writer_thread.start()
    
    def update_cutting_progress(self, value):
        self.log_status(f"Processing video... {value}%")
    
    def finish_cutting(self, used_codec):
        self.set_buttons_state(True)
        
        self.log_status(f"Video processed and exported successfully using codec {used_codec}!")
        
        QMessageBox.information(
            self, 
            "Completed", 
            f"Video processed and exported successfully!\nCodec used: {used_codec}"
        )
    
    def cancel_operation(self):
        if self.detection_thread and self.detection_thread.isRunning():
            self.detection_thread.stop()
            self.detection_thread.wait()
            self.log_status("Face detection canceled.")
        elif self.writer_thread and self.writer_thread.isRunning():
            self.writer_thread.stop()
            self.writer_thread.wait()
            self.log_status("Video processing canceled.")
            
        self.set_buttons_state(True)
        if self.video_path:
            self.detect_button.setEnabled(True)
        if len(self.selected_persons) > 0:
            self.cut_button.setEnabled(True)
    
    def set_buttons_state(self, enabled):
        self.load_button.setEnabled(enabled)
        self.detect_button.setEnabled(enabled and self.video_path is not None)
        self.cut_button.setEnabled(enabled and len(self.selected_persons) > 0)
        self.cancel_button.setEnabled(False)
        self.tolerance_slider.setEnabled(enabled)
    
    def log_status(self, message):
        self.status_log.append(f"Status: {message}")
        self.status_log.verticalScrollBar().setValue(
            self.status_log.verticalScrollBar().maximum()
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FaceBasedVideoCutter()
    window.show()
    sys.exit(app.exec())