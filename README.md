# NCHU **Algorithm** Course Assignment Submission Platform (Deploy, Backend)
> For Frontend related modifications, please refer to [IdONTKnowCHEK/OnlineJudgeFE-NCHU](https://github.com/IdONTKnowCHEK/OnlineJudgeFE-NCHU)

---

[ [English](README.md) | [繁體中文](README_zh.md) ]

This platform is modified based on [QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge), with several new features added to the Backend for course requirements:

1. Scripted the SSH configuration steps for containers in the DockerFile (to facilitate internal file modifications)
2. Each assignment is ranked by execution time or memory usage, and grades are assigned based on ranking
3. Assignments use Java with all import functions disabled by default, but can be selectively enabled as needed
4. Modified the original Special Judge function to use JSON format for setting assignment deadlines, allowed imports, and ranking methods
5. Added `Expired` status to Judge results to mark late submissions

## Deploy Step
1. Clone this repo
    ```bash
    git clone https://github.com/Rui0828/algo_oj_backend-NCHU-CSE.git
    ```

2. Configure environment parameters file `.env`
    ```ini
    export JUDGE_SERVER_TOKEN=TOKEN
    export BE_USERNAME=Backend-SSH-Username
    export BE_PASSWORD=Backend-SSH-Password
    ```

3. Start Docker Container
    ```bash
    docker-compose -p {container-name} up -d
    ```

## Ranking by Execution Time or Memory Usage
- The system automatically selects the best-performing version (time/memory) from each user's submissions for ranking

![Image](https://i.imgur.com/Kr2pufw.png)
![Image](https://i.imgur.com/FVAjkIp.png)

## Import Restrictions in Java (with Selective Allowances)
- If submitted code contains non-allowed import statements, the system will return `Compile Error`

![Image](https://i.imgur.com/jinUa2m.png)

## Special Judge Function Modified to Special Settings
- Uses JSON format for configuration

![Image](https://i.imgur.com/oQIl1XL.png)

- `"expire_time": "2025-3-28T14:00:00"` sets assignment deadline (default: no deadline)
- `"allowed_imports": ["java.util.Scanner"]` sets allowed imports (default: all disabled)
    - `java.util.*` means all packages in java.util are allowed
    - `*` means all packages are allowed
- `"rank_type": "time"` sets ranking type, options are `time` or `memory` (default: time)

## Late Submissions Display `Expired` Status
![Image](https://i.imgur.com/p3RdJtm.png)

## Original QDUOJ:
+ Backend (Django): [https://github.com/QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge)
+ Frontend (Vue): [https://github.com/QingdaoU/OnlineJudgeFE](https://github.com/QingdaoU/OnlineJudgeFE)
+ Judger Sandbox (Seccomp): [https://github.com/QingdaoU/Judger](https://github.com/QingdaoU/Judger)
+ JudgeServer (A wrapper for Judger): [https://github.com/QingdaoU/JudgeServer](https://github.com/QingdaoU/JudgeServer)

## License
[MIT](http://opensource.org/licenses/MIT)
