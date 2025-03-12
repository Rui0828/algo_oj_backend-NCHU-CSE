import hashlib
import json
import logging
from urllib.parse import urljoin

import requests
from django.db import transaction, IntegrityError
from django.db.models import F

from account.models import User
from conf.models import JudgeServer
from contest.models import ContestRuleType, ACMContestRank, OIContestRank, ContestStatus
from options.options import SysOptions
from problem.models import Problem, ProblemRuleType
from problem.utils import parse_problem_template
from submission.models import JudgeStatus, Submission
from utils.cache import cache
from utils.constants import CacheKey

import pytz
from datetime import datetime

logger = logging.getLogger(__name__)


# 继续处理在队列中的问题
def process_pending_task():
    if cache.llen(CacheKey.waiting_queue):
        # 防止循环引入
        from judge.tasks import judge_task
        tmp_data = cache.rpop(CacheKey.waiting_queue)
        if tmp_data:
            data = json.loads(tmp_data.decode("utf-8"))
            judge_task.send(**data)


class ChooseJudgeServer:
    def __init__(self):
        self.server = None

    def __enter__(self) -> [JudgeServer, None]:
        with transaction.atomic():
            servers = JudgeServer.objects.select_for_update().filter(is_disabled=False).order_by("task_number")
            servers = [s for s in servers if s.status == "normal"]
            for server in servers:
                if server.task_number <= server.cpu_core * 2:
                    server.task_number = F("task_number") + 1
                    server.save(update_fields=["task_number"])
                    self.server = server
                    return server
        return None

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.server:
            JudgeServer.objects.filter(id=self.server.id).update(task_number=F("task_number") - 1)


class DispatcherBase(object):
    def __init__(self):
        self.token = hashlib.sha256(SysOptions.judge_server_token.encode("utf-8")).hexdigest()

    def _request(self, url, data=None):
        kwargs = {"headers": {"X-Judge-Server-Token": self.token}}
        if data:
            kwargs["json"] = data
        try:
            return requests.post(url, **kwargs).json()
        except Exception as e:
            logger.exception(e)


class SPJCompiler(DispatcherBase):
    def __init__(self, spj_code, spj_version, spj_language):
        super().__init__()
        spj_compile_config = list(filter(lambda config: spj_language == config["name"], SysOptions.spj_languages))[0]["spj"][
            "compile"]
        self.data = {
            "src": spj_code,
            "spj_version": spj_version,
            "spj_compile_config": spj_compile_config
        }

    def compile_spj(self):
        with ChooseJudgeServer() as server:
            if not server:
                return "No available judge_server"
            result = self._request(urljoin(server.service_url, "compile_spj"), data=self.data)
            if not result:
                return "Failed to call judge server"
            if result["err"]:
                return result["data"]


class JudgeDispatcher(DispatcherBase):
    def __init__(self, submission_id, problem_id):
        super().__init__()
        self.submission = Submission.objects.get(id=submission_id)
        self.contest_id = self.submission.contest_id
        self.last_result = self.submission.result if self.submission.info else None

        if self.contest_id:
            self.problem = Problem.objects.select_related("contest").get(id=problem_id, contest_id=self.contest_id)
            self.contest = self.problem.contest
        else:
            self.problem = Problem.objects.get(id=problem_id)

    def _compute_statistic_info(self, resp_data):
        # 用时和内存占用保存为多个测试点中最长的那个
        self.submission.statistic_info["time_cost"] = max([x["cpu_time"] for x in resp_data])
        self.submission.statistic_info["memory_cost"] = max([x["memory"] for x in resp_data])

        # sum up the score in OI mode
        if self.problem.rule_type == ProblemRuleType.OI:
            score = 0
            try:
                for i in range(len(resp_data)):
                    if resp_data[i]["result"] == JudgeStatus.ACCEPTED:
                        resp_data[i]["score"] = self.problem.test_case_score[i]["score"]
                        score += resp_data[i]["score"]
                    else:
                        resp_data[i]["score"] = 0
            except IndexError:
                logger.error(f"Index Error raised when summing up the score in problem {self.problem.id}")
                self.submission.statistic_info["score"] = 0
                return
            self.submission.statistic_info["score"] = score

    def judge(self):
        language = self.submission.language
        sub_config = list(filter(lambda item: language == item["name"], SysOptions.languages))[0]
        
        
        # ✅ 解析 spj_code 存储的 JSON 数据（如果有值）
        expire_time, allowed_imports, late_allowed, late_until = None, None, None, None  # None 表示 "未设置"

        if self.problem.spj_code:  # ✅ 只有 SPJ 代码不为空时才进行检查
            try:
                spj_config_data = json.loads(self.problem.spj_code)  # 解析 `spj_code` 里的 JSON
                expire_time = spj_config_data.get("expire_time", None)  # 获取截止时间
                allowed_imports = spj_config_data.get("allowed_imports", None)  # 获取允许的 import (可选)
                late_allowed = spj_config_data.get("late_allowed", [])  # 获取允许遲交的用户名单
                late_until = spj_config_data.get("late_until", None)  # 获取遲交用户的最终截止时间
            except:
                self.submission.result = JudgeStatus.SYSTEM_ERROR
                self.submission.statistic_info = {"err_info": "Setting error: Setting format error"}
                self.submission.save(update_fields=["result", "statistic_info"])
                return

        if expire_time:
            # ✅ 获取台湾时区
            taipei_tz = pytz.timezone("Asia/Taipei")

            try:
                # ✅ 解析 JSON 里的 expire_time（无时区）
                expire_time_dt = datetime.strptime(expire_time, "%Y-%m-%dT%H:%M:%S")

                # ✅ 设定 expire_time 为台湾时区
                expire_time_dt = taipei_tz.localize(expire_time_dt)

                # ✅ 获取当前时间（台湾时区）
                now_time = datetime.now(taipei_tz)
                
                # ✅ 检查提交用户是否在允许遲交名单中
                username = User.objects.get(id=self.submission.user_id).username
                is_late_allowed = username in late_allowed if late_allowed else False
                
                # ✅ 如果在遲交名单中，需要检查是否超过遲交截止时间
                if is_late_allowed and late_until:
                    late_until_dt = datetime.strptime(late_until, "%Y-%m-%dT%H:%M:%S")
                    late_until_dt = taipei_tz.localize(late_until_dt)
                    
                    if now_time > late_until_dt:
                        self.submission.result = JudgeStatus.EXPIRED
                        self.submission.statistic_info = {"err_info": "Late submission deadline has passed."}
                        self.submission.save(update_fields=["result", "statistic_info"])
                        return
                # ✅ 如果不在遲交名单中，使用正常截止时间
                elif not is_late_allowed and now_time > expire_time_dt:
                    self.submission.result = JudgeStatus.EXPIRED
                    self.submission.statistic_info = {"err_info": "Submission deadline has passed."}
                    self.submission.save(update_fields=["result", "statistic_info"])
                    return
            except ValueError:
                # ✅ 处理 `expire_time` 或 `late_until` 解析失败（格式错误）
                self.submission.result = JudgeStatus.SYSTEM_ERROR
                self.submission.statistic_info = {"err_info": "Setting error: Time format error"}
                self.submission.save(update_fields=["result", "statistic_info"])
                return

        # ✅ 处理 Java `import` 限制
        if self.submission.language == "Java":
            # ✅ 首先处理 import 语句
            for line in self.submission.code.split("\n"):
                words = line.split()
                if words and words[0] == "import":  # ✅ 确保至少有两个单词
                    if len(words) < 2:  # ❌ 只包含 "import" 关键字，报错
                        self.submission.result = JudgeStatus.COMPILE_ERROR
                        self.submission.statistic_info = {
                            "err_info": "Invalid import statement."
                        }
                        self.submission.save(update_fields=["result", "statistic_info"])
                        return

                    imported_lib = words[1].strip(";")  # ✅ 现在可以安全获取导入的包名

                    if self.problem.spj_code:  # ✅ 只有 `spj_code` 存在时才检查 `allowed_imports`
                        if allowed_imports is None:  # ✅ 没有定义 `allowed_imports`，禁止所有 `import`
                            self.submission.result = JudgeStatus.COMPILE_ERROR
                            self.submission.statistic_info = {
                                "err_info": f"Import '{imported_lib}' is not allowed (all imports disabled)."
                            }
                            self.submission.save(update_fields=["result", "statistic_info"])
                            return

                        # ✅ 处理 `allowed_imports` 里的 `*`
                        allowed = False
                        for rule in allowed_imports:
                            if rule == "*":  # ✅ 处理 `*` 这种情况
                                allowed = True
                                break
                            elif rule.endswith(".*"):  # ✅ 处理 `java.util.*` 这种情况
                                package_prefix = rule[:-1]  # 去掉 `*`
                                if imported_lib.startswith(package_prefix):  # ✅ 允许 `java.util.XXX`
                                    allowed = True
                                    break
                            elif imported_lib == rule:  # ✅ 处理 `java.util.Scanner` 这种具体类
                                allowed = True
                                break

                        if not allowed:
                            self.submission.result = JudgeStatus.COMPILE_ERROR
                            self.submission.statistic_info = {
                                "err_info": f"Import '{imported_lib}' is not allowed."
                            }
                            self.submission.save(update_fields=["result", "statistic_info"])
                            return
                    else:
                        # ✅ 如果 `spj_code` 为空，则禁用所有 `import`
                        self.submission.result = JudgeStatus.COMPILE_ERROR
                        self.submission.statistic_info = {
                            "err_info": f"Import '{imported_lib}' is not allowed (all imports disabled)."
                        }
                        self.submission.save(update_fields=["result", "statistic_info"])
                        return
            
            # ✅ 检查完整限定类名的使用 (如 java.util.HashSet)
            if self.problem.spj_code and allowed_imports is not None:
                # 构建一个正则表达式来检测完整限定类名
                import re
                # 匹配格式: new java.util.XXX, java.util.XXX.method(), 变量声明 java.util.XXX var 等
                qualified_pattern = re.compile(r'(new\s+|^|\s+|<|,\s*)([a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z][a-zA-Z0-9]*)+)(\s*[(<]|\s+[a-zA-Z])')
                
                code_lines = self.submission.code.split('\n')
                for line_num, line in enumerate(code_lines, 1):
                    # 跳过注释行
                    if line.strip().startswith("//") or line.strip().startswith("/*") or line.strip().startswith("*"):
                        continue
                        
                    # 查找所有完整限定类名
                    matches = qualified_pattern.finditer(line)
                    for match in matches:
                        qualified_name = match.group(2)  # 获取匹配的完整限定名
                        
                        # 检查是否是Java标准库中的类
                        if qualified_name.startswith("java.") or qualified_name.startswith("javax."):
                            # 同样的检查逻辑，检查是否允许这个完整限定名
                            allowed = False
                            for rule in allowed_imports:
                                if rule == "*":
                                    allowed = True
                                    break
                                elif rule.endswith(".*"):
                                    package_prefix = rule[:-1]
                                    if qualified_name.startswith(package_prefix):
                                        allowed = True
                                        break
                                elif qualified_name == rule:
                                    allowed = True
                                    break
                                
                            if not allowed:
                                self.submission.result = JudgeStatus.COMPILE_ERROR
                                self.submission.statistic_info = {
                                    "err_info": f"Fully qualified class '{qualified_name}' is not allowed (line {line_num})."
                                }
                                self.submission.save(update_fields=["result", "statistic_info"])
                                return
            elif not self.problem.spj_code:
                # 如果无 spj_code，禁止使用任何 Java 标准库
                import re
                qualified_pattern = re.compile(r'(new\s+|^|\s+|<|,\s*)((java|javax)\.[a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z][a-zA-Z0-9]*)+)(\s*[(<]|\s+[a-zA-Z])')
                
                code_lines = self.submission.code.split('\n')
                for line_num, line in enumerate(code_lines, 1):
                    if line.strip().startswith("//") or line.strip().startswith("/*") or line.strip().startswith("*"):
                        continue
                        
                    matches = qualified_pattern.finditer(line)
                    for match in matches:
                        qualified_name = match.group(2)
                        self.submission.result = JudgeStatus.COMPILE_ERROR
                        self.submission.statistic_info = {
                            "err_info": f"Fully qualified class '{qualified_name}' is not allowed (all imports disabled, line {line_num})."
                        }
                        self.submission.save(update_fields=["result", "statistic_info"])
                        return
        
        
        
        # spj_config = {}
        # if self.problem.spj_code:
        #     for lang in SysOptions.spj_languages:
        #         if lang["name"] == self.problem.spj_language:
        #             spj_config = lang["spj"]
        #             break

        if language in self.problem.template:
            template = parse_problem_template(self.problem.template[language])
            code = f"{template['prepend']}\n{self.submission.code}\n{template['append']}"
        else:
            code = self.submission.code

        data = {
            "language_config": sub_config["config"],
            "src": code,
            "max_cpu_time": self.problem.time_limit,
            "max_memory": 1024 * 1024 * self.problem.memory_limit,
            "test_case_id": self.problem.test_case_id,
            
            # "output": False,
            # "spj_version": self.problem.spj_version,
            # "spj_config": spj_config.get("config"),
            # "spj_compile_config": spj_config.get("compile"),
            # "spj_src": self.problem.spj_code,
            
            "output": True,  # ✅ 强制使用 `test_case_output` 进行评测
            "spj_version": None,
            "spj_config": None,
            "spj_compile_config":  None,
            "spj_src": None,
            
            "io_mode": self.problem.io_mode
            
        }

        with ChooseJudgeServer() as server:
            if not server:
                data = {"submission_id": self.submission.id, "problem_id": self.problem.id}
                cache.lpush(CacheKey.waiting_queue, json.dumps(data))
                return
            Submission.objects.filter(id=self.submission.id).update(result=JudgeStatus.JUDGING)
            resp = self._request(urljoin(server.service_url, "/judge"), data=data)

        if not resp:
            Submission.objects.filter(id=self.submission.id).update(result=JudgeStatus.SYSTEM_ERROR)
            return

        if resp["err"]:
            self.submission.result = JudgeStatus.COMPILE_ERROR
            self.submission.statistic_info["err_info"] = resp["data"]
            self.submission.statistic_info["score"] = 0
        else:
            resp["data"].sort(key=lambda x: int(x["test_case"]))
            self.submission.info = resp
            self._compute_statistic_info(resp["data"])
            error_test_case = list(filter(lambda case: case["result"] != 0, resp["data"]))
            # ACM模式下,多个测试点全部正确则AC，否则取第一个错误的测试点的状态
            # OI模式下, 若多个测试点全部正确则AC， 若全部错误则取第一个错误测试点状态，否则为部分正确
            if not error_test_case:
                self.submission.result = JudgeStatus.ACCEPTED
            elif self.problem.rule_type == ProblemRuleType.ACM or len(error_test_case) == len(resp["data"]):
                self.submission.result = error_test_case[0]["result"]
            else:
                self.submission.result = JudgeStatus.PARTIALLY_ACCEPTED
        self.submission.save()

        if self.contest_id:
            if self.contest.status != ContestStatus.CONTEST_UNDERWAY or \
                    User.objects.get(id=self.submission.user_id).is_contest_admin(self.contest):
                logger.info(
                    "Contest debug mode, id: " + str(self.contest_id) + ", submission id: " + self.submission.id)
                return
            with transaction.atomic():
                self.update_contest_problem_status()
                self.update_contest_rank()
        else:
            if self.last_result:
                self.update_problem_status_rejudge()
            else:
                self.update_problem_status()

        # 至此判题结束，尝试处理任务队列中剩余的任务
        process_pending_task()

    def update_problem_status_rejudge(self):
        result = str(self.submission.result)
        problem_id = str(self.problem.id)
        with transaction.atomic():
            # update problem status
            problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
            if self.last_result != JudgeStatus.ACCEPTED and self.submission.result == JudgeStatus.ACCEPTED:
                problem.accepted_number += 1
            problem_info = problem.statistic_info
            problem_info[self.last_result] = problem_info.get(self.last_result, 1) - 1
            problem_info[result] = problem_info.get(result, 0) + 1
            problem.save(update_fields=["accepted_number", "statistic_info"])

            profile = User.objects.select_for_update().get(id=self.submission.user_id).userprofile
            if problem.rule_type == ProblemRuleType.ACM:
                acm_problems_status = profile.acm_problems_status.get("problems", {})
                if acm_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    acm_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        profile.accepted_number += 1
                profile.acm_problems_status["problems"] = acm_problems_status
                profile.save(update_fields=["accepted_number", "acm_problems_status"])

            else:
                oi_problems_status = profile.oi_problems_status.get("problems", {})
                score = self.submission.statistic_info["score"]
                if oi_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    # minus last time score, add this tim score
                    profile.add_score(this_time_score=score,
                                      last_time_score=oi_problems_status[problem_id]["score"])
                    oi_problems_status[problem_id]["score"] = score
                    oi_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        profile.accepted_number += 1
                profile.oi_problems_status["problems"] = oi_problems_status
                profile.save(update_fields=["accepted_number", "oi_problems_status"])

    def update_problem_status(self):
        result = str(self.submission.result)
        problem_id = str(self.problem.id)
        with transaction.atomic():
            # update problem status
            problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
            problem.submission_number += 1
            if self.submission.result == JudgeStatus.ACCEPTED:
                problem.accepted_number += 1
            problem_info = problem.statistic_info
            problem_info[result] = problem_info.get(result, 0) + 1
            problem.save(update_fields=["accepted_number", "submission_number", "statistic_info"])

            # update_userprofile
            user = User.objects.select_for_update().get(id=self.submission.user_id)
            user_profile = user.userprofile
            user_profile.submission_number += 1
            if problem.rule_type == ProblemRuleType.ACM:
                acm_problems_status = user_profile.acm_problems_status.get("problems", {})
                if problem_id not in acm_problems_status:
                    acm_problems_status[problem_id] = {"status": self.submission.result, "_id": self.problem._id}
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                elif acm_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    acm_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                user_profile.acm_problems_status["problems"] = acm_problems_status
                user_profile.save(update_fields=["submission_number", "accepted_number", "acm_problems_status"])

            else:
                oi_problems_status = user_profile.oi_problems_status.get("problems", {})
                score = self.submission.statistic_info["score"]
                if problem_id not in oi_problems_status:
                    user_profile.add_score(score)
                    oi_problems_status[problem_id] = {"status": self.submission.result,
                                                      "_id": self.problem._id,
                                                      "score": score}
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                elif oi_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    # minus last time score, add this time score
                    user_profile.add_score(this_time_score=score,
                                           last_time_score=oi_problems_status[problem_id]["score"])
                    oi_problems_status[problem_id]["score"] = score
                    oi_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                user_profile.oi_problems_status["problems"] = oi_problems_status
                user_profile.save(update_fields=["submission_number", "accepted_number", "oi_problems_status"])

    def update_contest_problem_status(self):
        with transaction.atomic():
            user = User.objects.select_for_update().get(id=self.submission.user_id)
            user_profile = user.userprofile
            problem_id = str(self.problem.id)
            if self.contest.rule_type == ContestRuleType.ACM:
                contest_problems_status = user_profile.acm_problems_status.get("contest_problems", {})
                if problem_id not in contest_problems_status:
                    contest_problems_status[problem_id] = {"status": self.submission.result, "_id": self.problem._id}
                elif contest_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    contest_problems_status[problem_id]["status"] = self.submission.result
                else:
                    # 如果已AC， 直接跳过 不计入任何计数器
                    return
                user_profile.acm_problems_status["contest_problems"] = contest_problems_status
                user_profile.save(update_fields=["acm_problems_status"])

            elif self.contest.rule_type == ContestRuleType.OI:
                contest_problems_status = user_profile.oi_problems_status.get("contest_problems", {})
                score = self.submission.statistic_info["score"]
                if problem_id not in contest_problems_status:
                    contest_problems_status[problem_id] = {"status": self.submission.result,
                                                           "_id": self.problem._id,
                                                           "score": score}
                else:
                    contest_problems_status[problem_id]["score"] = score
                    contest_problems_status[problem_id]["status"] = self.submission.result
                user_profile.oi_problems_status["contest_problems"] = contest_problems_status
                user_profile.save(update_fields=["oi_problems_status"])

            problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
            result = str(self.submission.result)
            problem_info = problem.statistic_info
            problem_info[result] = problem_info.get(result, 0) + 1
            problem.submission_number += 1
            if self.submission.result == JudgeStatus.ACCEPTED:
                problem.accepted_number += 1
            problem.save(update_fields=["submission_number", "accepted_number", "statistic_info"])

    def update_contest_rank(self):
        if self.contest.rule_type == ContestRuleType.OI or self.contest.real_time_rank:
            cache.delete(f"{CacheKey.contest_rank_cache}:{self.contest.id}")

        def get_rank(model):
            return model.objects.select_for_update().get(user_id=self.submission.user_id, contest=self.contest)

        if self.contest.rule_type == ContestRuleType.ACM:
            model = ACMContestRank
            func = self._update_acm_contest_rank
        else:
            model = OIContestRank
            func = self._update_oi_contest_rank

        try:
            rank = get_rank(model)
        except model.DoesNotExist:
            try:
                model.objects.create(user_id=self.submission.user_id, contest=self.contest)
                rank = get_rank(model)
            except IntegrityError:
                rank = get_rank(model)
        func(rank)

    def _update_acm_contest_rank(self, rank):
        info = rank.submission_info.get(str(self.submission.problem_id))
        # 因前面更改过，这里需要重新获取
        problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
        # 此题提交过
        if info:
            if info["is_ac"]:
                return

            rank.submission_number += 1
            if self.submission.result == JudgeStatus.ACCEPTED:
                rank.accepted_number += 1
                info["is_ac"] = True
                info["ac_time"] = (self.submission.create_time - self.contest.start_time).total_seconds()
                rank.total_time += info["ac_time"] + info["error_number"] * 20 * 60

                if problem.accepted_number == 1:
                    info["is_first_ac"] = True
            elif self.submission.result != JudgeStatus.COMPILE_ERROR:
                info["error_number"] += 1

        # 第一次提交
        else:
            rank.submission_number += 1
            info = {"is_ac": False, "ac_time": 0, "error_number": 0, "is_first_ac": False}
            if self.submission.result == JudgeStatus.ACCEPTED:
                rank.accepted_number += 1
                info["is_ac"] = True
                info["ac_time"] = (self.submission.create_time - self.contest.start_time).total_seconds()
                rank.total_time += info["ac_time"]

                if problem.accepted_number == 1:
                    info["is_first_ac"] = True

            elif self.submission.result != JudgeStatus.COMPILE_ERROR:
                info["error_number"] = 1
        rank.submission_info[str(self.submission.problem_id)] = info
        rank.save()

    def _update_oi_contest_rank(self, rank):
        problem_id = str(self.submission.problem_id)
        current_score = self.submission.statistic_info["score"]
        last_score = rank.submission_info.get(problem_id)
        if last_score:
            rank.total_score = rank.total_score - last_score + current_score
        else:
            rank.total_score = rank.total_score + current_score
        rank.submission_info[problem_id] = current_score
        rank.save()
