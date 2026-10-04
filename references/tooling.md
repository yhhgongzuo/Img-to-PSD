# 参数、格式与命令

所有命令中的路径按本次环境替换；脚本目录相对于本 Skill。PowerShell 使用实际 Python 路径加 `-X utf8`。依赖 Pillow、numpy、opencv-python、psd-tools。先检查再安装缺项。工作文件在当前任务 `work/`，交付按用户指定位置。

## manifest.json

```json
{
  "elements": [
    {"id":"cake","name":"月饼","route":"generated","components":["cake-body"],"shadow":"pair","receiver":"plate"},
    {"id":"plate","name":"空盘","route":"generated","components":["plate-body"],"shadow":"pair","receiver":"background"},
    {"id":"title","name":"标题","route":"native-text","components":["title-text"],"shadow":"none","shadow_reason":"平面文字"},
    {"id":"background","name":"纯背景","route":"generated-background","components":["background-image"],"shadow":"none","shadow_reason":"完整场景承接面"}
  ],
  "overlaps": [["cake","plate"]],
  "batches": [
    {"id":"A","elements":["cake"],"background":"green","color_reason":"前景无绿色，已经检查暗部及反光"},
    {"id":"B","elements":["plate"],"background":"magenta","color_reason":"青瓷无紫色"},
    {"id":"BG","elements":["background"],"background":"scene","color_reason":"独立完整背景"}
  ]
}
```

其他 route：`native-shape`、`effect`。可记录 original_bounds、rotation、material、light、occluded_by、generation_path 等。`overlaps` 来自实际像素/遮挡，不等于包围框相交。运行 `python -X utf8 scripts/plan.py manifest.json`。脚本只验证已声明清单的覆盖/冲突，四轮目视清点不可省。

## 透明操作

`samples.json` 是整批物件内部代表 RGB 数组，如 `[[170,65,180],[232,202,120]]`，排除背景，包含细节/暗部/反光。不是只抽一个平均色。

```powershell
python -X utf8 scripts/asset_ops.py choose samples.json
python -X utf8 scripts/asset_ops.py inspect generated.png
python -X utf8 scripts/asset_ops.py matte generated-green.png transparent.png --key green --samples samples.json
python -X utf8 scripts/asset_ops.py split transparent.png regions.json assets
```

支持六种技术底，选色仅保守辅助。`matte` 从边界安全底色候选估计背景，允许原有出画物件；边界至少需足够背景样本。拒绝重复处理已有 alpha、疑似棋盘格、前景色冲突。通过检测不证明没有纹理/残色；先看原生成背景。色差假设不适合任意玻璃/彩色半透明，失败换原生 alpha/底色/批次，不任意放宽阈值。

`regions.json` 如 `{"cake-body":[0,0,400,400],"knife-body":[400,0,800,400]}`，坐标左上右下（右/下不含）。内部边界透明、区域不重叠，允许物件接触原画布边界。导出每件仍保持输入整张画布及坐标，全部可见 alpha 须分配，细小岛不丢。漏像素时报错，输出视为暂存，修正区域后用新目录；不能删掉真实细节过检。连体部件依物理接缝处理，不硬裁轮廓。

`asset_ops.py refine-mixed input.png output.png` 仅用于已诊断的单一材料混色边，最近高 alpha 颜色参考不侵蚀，且原 alpha 精确保留。默认不调用；跨材料、高光梯度、玻璃/虚焦可导致颜色扩散，需先局部试验。它不修不透明区域残色；最终原生色域修复及实际背景目视仍必要。

## 阴影轮廓

使用已通过几何关口的新生成全画布 alpha；不可传紧裁图。原画布外的投影裁切正常，内部阴影不应被素材小画布截断。

```powershell
python -X utf8 scripts/asset_ops.py shadow object-canvas.png contact.png --mode base --dx 0.5 --dy 0.5 --back 2 --front 2 --min-y 100
python -X utf8 scripts/asset_ops.py shadow object-canvas.png soft.png --mode base --dx 12 --dy 7 --back 15 --front 8 --min-y 100
```

示例参数不可照搬。`base` 跟随每列真实下缘，`min-y` 排除悬空上部；`flat` 用薄物件自身轮廓。脚本不懂真实支撑点和承接面，人物腿间、悬挑、跨面投影须结合场景另做可编辑修正。输出无模糊无烘焙不透明度的暗色 RGBA；PS 中设 Multiply、不透明度和 `postBlur`，两层参数独立。

## scene.json 与 Photoshop

根字段：`name,sourceImage,width,height,resolution,outputPsd,outputJpg,qaPng,groups`，路径必须绝对。组支持 `name,visible,element_id,layers,groups`。所有名称全局唯一。数组按从下到上构建；组内先 layers 再 groups，所以混合层级时需依遮挡重组，不能假设 JSON 任意顺序会自动修正。

层格式：

- 图像：`{"type":"image","name":"月饼主体","path":"绝对PNG路径","component_id":"cake-body","role":"object"}`。全部图像（含背景、参考、阴影）预先归一至 scene 全画布；不设 frame，不紧裁。EXIF 归正、超 4096 等比缩小的隐藏参考也另存预处理副本。可记录已应用的 `registration:[[a,-b,tx],[b,a,ty]]`，预检拒绝非相似矩阵；composer 不再次应用该元数据。
- 文字：`{"type":"text","name":"标题","component_id":"title-text","text":"原文","font":"已安装PostScript字体名","size":48,"x":30,"y":80,"color":"FFFFFF"}`。size 是像素，y 是基线；支持 tracking、align。fitBox 为 `[left,top,right,bottom]`，仅在有依据时适配，优先自然字体参数。
- 图形：`{"type":"fill","name":"装饰色块","component_id":"shape-id","color":"E0C580","shape":"rectangle","box":[left,top,right,bottom]}`；ellipse 可选。无 blur 为原生形状，带 blur 转智能对象并加高斯智能滤镜。
- 公共：opacity 0～100、blend（如 MULTIPLY/SCREEN）、visible、clipped。
- 图像 `postBlur` 为智能高斯；`brightness`/`contrast` 为智能亮度对比度；`rgbOffsets:[r,g,b]` 使用 9 点曲线。顺序为 blur → 亮度对比度 → RGB → hue。不要传任意稀疏 rgbCurves。
- 诊断后才设置 `hueAdjustments:[{"channel":6,"range":[255,285,315,345],"hue":90,"saturation":0,"lightness":0}]`。channel 1..6 和四个明确范围必须同时提供，防误用主通道；这只是洋红污染金色的示例，不能作为默认预设。参阅 v3-lessons。
- `exportPng` 是该组件最终原生滤镜后全画布导出路径；独立导出要求该层无剪贴、Normal、100% opacity。若需要外部调整层/组效果或跨层交互，另做专用隔离导出并验证，不能套此单层导出。
- 纯颜色修复可声明 `alphaBaseline`（修前最终 PNG），验证导出的 alpha 逐像素不变。几何/景深真的改变时不设置此约束。
- 阴影 role 为 `soft-shadow`/`contact-shadow`，图层顺序软影、实影、主体。所在组 `element_id` 对应 manifest 双影元素；该组包含它的所有主体部件。阴影无 component_id（清单的 shadow 字段隐含这两层）。参考层 `role:"reference"`，参考组隐藏。

原生 QA：设 `qaMoveGroup` 为一个有双影的物件组名。`qaViews` 为 `[{"path":"绝对PNG路径","onlyRoot":"背景组名"},{"path":"另一PNG路径","hide":["月饼组名"]}]`；可输出空承接面或按阴影层名列表输出无影图。设置所需全部视图，脚本不猜测。

```powershell
& scripts/build_psd.ps1 -Spec scene.json -Python '实际python.exe路径'
python -X utf8 scripts/verify_psd.py scene.json manifest.json qa.json
```

执行器先运行 preflight：全画布、无 frame、合法颜色范围/相似矩阵，然后拒覆盖已有 PSD/JPG/QA/透明导出。UTF-8 JSON 注入 JSX，递归创建原生层、保存重开、编辑/移动恢复、导出，再恢复活动文档。只关闭自己创建的文档；不覆盖/关闭用户未保存文档。

## 局部修正与复用

保留完整 scene，在根部加 `basePsd`（已有 PSD 绝对路径），只对需替换图像设置 `patch:true`。脚本复制源文档，在原组/原顺序替换同名层，其他层保持；输入未经重复校准的 raw 全画布 PNG + 完整滤镜声明。不更名组，不改未 patch 项，不自动修改组结构或原生文字。输出使用全新版本路径。只改颜色则沿用原影；改轮廓/位置时将对应影层一并 patch。

未 patch 的 scene 项必须仍反映基准 PSD 的真实状态。保存原 PSD 哈希和保护组件 PNG/hash，运行 verify 后比较；不得仅凭场景声明认定保护项未变。若有新的组结构、跨层效果、文字内容变化，使用专用原生编辑并验证，而非本图像替换模式。

验收脚本核对 manifest 全部 component_id、双影同组、层类型、真实 alpha、隐藏参考、源图画布规则及重开合成；其通过只证明结构检查。按 workflow 做所有目视检查并另写 `visual_qa.md`。复杂形状/特殊滤镜可扩展本次 JSX，但必须保留上述约束并重新验证。

维护后运行 `python -B -X utf8 scripts/self_test.py`，隔离测试六底色、真 alpha、全画布位置、内部切边/原边裁切、窄深边颜色参考、比例/滤镜预检、阴影与清单。涉及 JSX 改动时另运行原生回归：`python -B -X utf8 scripts/native_test.py 全新绝对测试目录`，然后按输出命令生成和验证。只用合成测试素材，不调用图像生成、不修改用户作品。
