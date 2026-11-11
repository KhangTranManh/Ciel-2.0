# Ciel VTuber

Thư mục độc lập dành cho quá trình chuyển thiết kế Ciel thành model VTuber 2D.
Toàn bộ ảnh nguồn, layer trung gian, PSD, file Live2D và báo cáo QA của dự án này
phải nằm trong `vtuber_Ciel/`, không đặt rời rạc ở thư mục gốc của repository.

## Mục tiêu V1

Tạo model Ciel dạng bust-up (từ đầu đến khoảng hông) chạy được trong VTube Studio,
với các chuyển động:

- Quay đầu nhẹ theo `X`, `Y`, `Z`.
- Chớp mắt và hướng nhìn theo `X`, `Y`.
- Chuyển động lông mày và 3-5 biểu cảm cơ bản.
- Miệng mở/khép, mouth form và lip-sync.
- Nhịp thở nhẹ.
- Physics cho tóc trước, tóc sau, tóc hai bên và phụ kiện.
- Đuôi chuyển động idle nhẹ nếu còn trong khung hình.

V1 chưa bao gồm hand tracking, full-body tracking, chuyển động chân, thay trang phục
hoặc góc quay đầu lớn.

## Pipeline tổng quát

```text
Ảnh nguồn
  -> định nghĩa layer manifest
  -> SeeThrough chạy trên AWS GPU
  -> kiểm tra và sửa semantic masks/depth
  -> vẽ bù vùng bị che bằng inpainting hoặc chỉnh tay
  -> PSD chuẩn Live2D
  -> rig trong Live2D Cubism
  -> QA
  -> export sang VTube Studio
```

SeeThrough chỉ tạo bản PSD phân lớp ban đầu. Nó không tự tạo rig, parameter,
deformer hay model VTuber hoàn chỉnh.

## Phase 0 - Kiểm tra AWS GPU

Lấy tên GPU, VRAM và driver bằng:

```bash
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
```

Model inpainting sẽ được chọn sau khi biết VRAM:

- 8-12 GB: SeeThrough và SD 1.5 inpainting trên crop nhỏ.
- 16-24 GB: SeeThrough và SDXL inpainting.
- Từ 24 GB: có thể thử model inpainting lớn hơn, nhưng vẫn chỉ sửa từng vùng nhỏ.

AWS chỉ cần dùng cho phân lớp, depth và inpainting. Công đoạn rig Live2D có thể làm
trên máy cá nhân.

### AWS đã xác nhận

- Instance: `g6.xlarge` (Amazon Linux 2023).
- GPU: NVIDIA L4, 23,034 MiB VRAM.
- Driver: `595.112.07`.
- Tại thời điểm kiểm tra: khoảng 5,990 MiB đang được một tiến trình Python sử dụng,
  còn khoảng 16,576 MiB trống.
- Dung lượng đĩa trống: khoảng 58 GB.
- Không dừng hoặc sửa tiến trình đang chạy. Mọi file VTuber dùng workspace riêng
  `~/vtuber_Ciel`.

Với lượng VRAM còn trống, baseline là SeeThrough V3 ở độ phân giải 1280 với
`--group_offload`. Pipeline chính thức dùng LayerDiff 3D (SDXL) để tạo layer trong
suốt, Marigold được fine-tune để ước lượng depth và body-part parsing để gắn semantic
tag. Inpainting riêng chỉ dùng để sửa những vùng không đạt sau QA. Trước mỗi job GPU
phải kiểm tra lại `nvidia-smi`; nếu còn dưới 14 GB thì dừng, không kill tiến trình khác
và không tự hạ chất lượng âm thầm.

Repository chính thức được khóa tại commit
`df019de5129d6c4b406587a14c3501669441a783`. Lệnh chạy tái lập nằm ở
`scripts/run_seethrough.sh`.

## Phase 1 - Layer manifest

Cấu trúc dự kiến cho V1:

```text
Ciel
|-- Tail
|   |-- tail_base
|   |-- tail_middle
|   `-- tail_tip
|-- BackHair
|   |-- back_hair_center
|   |-- back_hair_L
|   |-- back_hair_R
|   |-- side_hair_L
|   `-- side_hair_R
|-- Body
|   |-- torso
|   |-- neck
|   |-- white_top
|   |-- yellow_collar
|   |-- shoulder_accessory_L
|   |-- shoulder_accessory_R
|   |-- arm_L
|   |-- arm_R
|   |-- black_sleeve_L
|   |-- black_sleeve_R
|   |-- hand_L
|   `-- hand_R
|-- Head
|   |-- face_base
|   |-- ear_L
|   |-- ear_R
|   |-- eyebrow_L
|   |-- eyebrow_R
|   |-- eye_L
|   |   |-- eyewhite
|   |   |-- iris
|   |   |-- pupil
|   |   |-- highlight
|   |   |-- upper_lash
|   |   `-- lower_lash
|   |-- eye_R
|   |   |-- eyewhite
|   |   |-- iris
|   |   |-- pupil
|   |   |-- highlight
|   |   |-- upper_lash
|   |   `-- lower_lash
|   |-- nose
|   `-- mouth
|       |-- mouth_line
|       |-- upper_lip
|       |-- lower_lip
|       |-- mouth_inner
|       |-- tongue
|       `-- teeth
`-- FrontHair
    |-- bang_center
    |-- bang_L1
    |-- bang_L2
    |-- bang_R1
    |-- bang_R2
    `-- ahoge_or_top_hair
```

Dự kiến khoảng 45-65 layer/ArtMesh cho V1. Có thể giữ nguồn full-body trong PSD,
nhưng trước mắt chỉ rig phần bust-up.

## Phase 2 - SeeThrough trên AWS

Đầu ra cần có:

- Semantic mask cho từng bộ phận.
- Layer RGBA.
- Depth map và thứ tự trước-sau ban đầu.
- Bounding box và tên layer.
- Reconstruction ghép lại từ toàn bộ layer.

Các lỗi cần kiểm tra thủ công:

- Tóc dính vào mặt.
- Tay áo đen dính với quần.
- Phụ kiện vàng dính với tóc hoặc da.
- Hai mắt bị gộp.
- Đuôi bị chia sai ở vùng giao với chân.
- Alpha còn viền trắng hoặc mất nét viền.

Điều kiện hoàn thành phase:

- Reconstruction gần giống ảnh nguồn.
- Không mất mắt, miệng, tay hoặc đuôi.
- Không có đường viền trắng rõ rệt.
- Mỗi layer có tên, bounds và vị trí xác định.

## Phase 3 - Vẽ bù vùng bị che

Các vùng dự kiến phải tái dựng:

- Khuôn mặt và trán phía dưới tóc mái.
- Tròng mắt đầy đủ phía sau mí mắt.
- Da và cổ phía sau tóc.
- Áo phía sau cánh tay.
- Tóc phía sau vai và tay.
- Phần đuôi bị cơ thể hoặc chân che.
- Khoang miệng dùng cho trạng thái mở.

Mỗi lần sửa cần giữ lại ảnh gốc, mask, candidate, kết quả được chọn và ảnh kiểm tra
khi layer bị kéo khỏi vị trí trung tâm. Không dùng AI để vẽ lại toàn bộ nhân vật.

## Phase 4 - PSD chuẩn Live2D

- Một layer không chứa nhiều bộ phận cần chuyển động độc lập.
- Tên layer không trùng và thể hiện rõ trái/phải.
- Giữ nguyên canvas và tọa độ của layer.
- Có đủ hidden coverage cho phạm vi chuyển động dự kiến.
- Neutral composite phải khớp ảnh nguồn.
- Luôn giữ riêng source, working copy và export copy.

## Phase 5 - Rig Live2D Cubism

Parameter cơ bản:

```text
ParamAngleX
ParamAngleY
ParamAngleZ
ParamBodyAngleX
ParamEyeLOpen
ParamEyeROpen
ParamEyeBallX
ParamEyeBallY
ParamBrowLY
ParamBrowRY
ParamMouthOpenY
ParamMouthForm
ParamBreath
```

Physics dự kiến:

- Tóc mái.
- Tóc hai bên.
- Tóc dài phía sau.
- Phụ kiện vàng.
- Đuôi.
- Chuyển động thân rất nhẹ.

## Phase 6 - QA và export

Kiểm tra tối thiểu:

- Neutral không khác ảnh nguồn rõ rệt.
- Quay đầu không làm hở mặt dưới tóc.
- Mắt nhắm không xuất hiện hai lớp mí hoặc lông mi.
- Miệng không méo, ghost hoặc đổi phong cách.
- Tóc không xuyên mặt và vai.
- Physics không rung liên tục.
- VTube Studio nhận đúng blink, gaze và lip-sync.

Đầu ra dự kiến:

```text
vtuber_Ciel/
|-- README.md
|-- source/
|   `-- ciel_source.png
|-- manifests/
|   `-- ciel_layers_manifest.json
|-- layers/
|-- inpainting/
|-- psd/
|   `-- ciel_layered.psd
|-- live2d/
|   |-- ciel_live2d.cmo3
|   |-- ciel_live2d.moc3
|   |-- textures/
|   |-- expressions/
|   |-- physics3.json
|   `-- model3.json
`-- reports/
    `-- qa-report.html
```

## Trạng thái hiện tại

- [x] Chọn hướng Live2D thay cho model 3D.
- [x] Xác định phạm vi bust-up V1.
- [x] Lập pipeline và layer manifest sơ bộ.
- [x] Ghi nhận GPU/VRAM của AWS.
- [x] Đưa ảnh nguồn vào `source/`.
- [x] Chốt layer manifest chi tiết tại `manifests/ciel_layers_manifest.json`.
- [x] Tạo môi trường Python 3.12/PyTorch CUDA cô lập trên AWS.
- [x] Kiểm tra CLI SeeThrough load thành công.
- [ ] Chạy SeeThrough trên AWS.
- [ ] Sửa layer và vẽ bù hidden coverage.
- [ ] Tạo PSD chuẩn Live2D.
- [ ] Rig và kiểm thử trong VTube Studio.
