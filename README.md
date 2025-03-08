# 中興大學 **演算法** 課程作業繳交平台 (Backend)
此平台基於 [QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge) 下去修改，新增幾個我們課程中會用到的功能。

1. 將 container 設定 SSH 的步驟腳本化，新增在 DockerFile 中。 (方便我們修改內部檔案)
2. 每次作業都會透過執行的 time 或 memory 進行排序，我們透過排名來給予作業成績。
3. 作業使用 Java 撰寫並禁用所有 import 功能，但可以特例開放。
4. 我們用不到 Special Judge，因此把它改成透過 json 的格式設定 作業 Deadline、允許 import 、排名方式。
5. Judge result 新增一個 `Expired` ，若遲交作業，將回傳此狀態。

---

原版 QDUOJ:
+ Backend(Django): [https://github.com/QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge)
+ Frontend(Vue): [https://github.com/QingdaoU/OnlineJudgeFE](https://github.com/QingdaoU/OnlineJudgeFE)
+ Judger Sandbox(Seccomp): [https://github.com/QingdaoU/Judger](https://github.com/QingdaoU/Judger)
+ JudgeServer(A wrapper for Judger): [https://github.com/QingdaoU/JudgeServer](https://github.com/QingdaoU/JudgeServer)

## 透過 time 或 memory 進行排序
- 每個使用者會擇優出 time/memory 最好的版本進行排名。

![Image](https://i.imgur.com/Kr2pufw.png)
![Image](https://i.imgur.com/FVAjkIp.png)


## java 中禁用 import (可以特例開放)
- 若繳交的 code 中包含非允需 import 會回傳 `Compile Error`。

![Image](https://i.imgur.com/jinUa2m.png)


##  Special Judge 改成拿來做特殊設定 
- 用 JSON 格式設定

![Image](https://i.imgur.com/oQIl1XL.png)

- `"expire_time": "2025-3-28T14:00:00"` 設定作業 deadline (default: 無 deadline)  
- `"allowed_imports": ["java.util.Scanner"]` 設定允許的 import (default: 禁用全部)  
    - `java.util.*` 代表所有 java.util. 皆可使用
    - `*` 全部開放
- `"rank_type": "time"` 設定排名類型 `time` 或 `memory` (default: time)


## 遲交作業，將回傳 `Expired` 狀態
![Image](https://i.imgur.com/p3RdJtm.png)


## License
[MIT](http://opensource.org/licenses/MIT)
