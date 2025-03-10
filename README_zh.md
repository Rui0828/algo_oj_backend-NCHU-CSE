# 中興大學 **演算法** 課程作業繳交平台 (Deploy、Backend)
[ [English](README.md) | [繁體中文](README_zh.md) ]

> Frontend 的相關修改請參考 [IdONTKnowCHEK/OnlineJudgeFE-NCHU](https://github.com/IdONTKnowCHEK/OnlineJudgeFE-NCHU)

---

此平台基於 [QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge) 修改而成，針對 Backend 新增了幾項課程所需的功能：

1. 將 container 設定 SSH 的步驟腳本化，新增在 DockerFile 中（方便修改內部檔案）
2. 每次作業會透過執行時間或記憶體使用量進行排序，依據排名給予作業成績
3. 作業使用 Java 撰寫並預設禁用所有 import 功能，但可依需求特例開放
4. 將原本的 Special Judge 功能改為透過 JSON 格式設定作業截止日期、允許的 import 及排名方式
5. Judge result 新增 `Expired` 狀態，標示遲交作業

## Deploy Step
1. Clone this repo
    ```bash
    git clone https://github.com/Rui0828/algo_oj_backend-NCHU-CSE.git
    ```

2. 設定環境參數檔案 `.env`
    ```ini
    export JUDGE_SERVER_TOKEN=TOKEN
    export BE_USERNAME=Backend-SSH-Username
    export BE_PASSWORD=Backend-SSH-Password
    ```

3. 啟動 Docker Container
    ```bash
    docker-compose -p {container-name} up -d
    ```


## 透過時間或記憶體使用量進行排序
- 系統會自動選取每位使用者提交中時間/記憶體表現最佳的版本進行排名

![Image](https://i.imgur.com/Kr2pufw.png)
![Image](https://i.imgur.com/FVAjkIp.png)


## Java 中禁用 import（可特例開放）
- 若繳交的程式碼中包含非允許的 import 語句，系統會回傳 `Compile Error`

![Image](https://i.imgur.com/jinUa2m.png)


## Special Judge 功能改為特殊設定 
- 使用 JSON 格式進行設定

![Image](https://i.imgur.com/oQIl1XL.png)

- `"expire_time": "2025-3-28T14:00:00"` 設定作業截止時間（預設：無截止時間）  
- `"allowed_imports": ["java.util.Scanner"]` 設定允許的 import（預設：全部禁用）  
    - `java.util.*` 代表所有 java.util 套件可使用
    - `*` 代表全部套件開放
- `"rank_type": "time"` 設定排名類型，可選 `time` 或 `memory`（預設：time）


## 遲交作業顯示 `Expired` 狀態
![Image](https://i.imgur.com/p3RdJtm.png)


## 原版 QDUOJ:
+ Backend (Django): [https://github.com/QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge)
+ Frontend (Vue): [https://github.com/QingdaoU/OnlineJudgeFE](https://github.com/QingdaoU/OnlineJudgeFE)
+ Judger Sandbox (Seccomp): [https://github.com/QingdaoU/Judger](https://github.com/QingdaoU/Judger)
+ JudgeServer (A wrapper for Judger): [https://github.com/QingdaoU/JudgeServer](https://github.com/QingdaoU/JudgeServer)

## License
[MIT](http://opensource.org/licenses/MIT)
