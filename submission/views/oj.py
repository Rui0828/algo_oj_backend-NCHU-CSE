import ipaddress
import json

from account.decorators import login_required, check_contest_permission
from contest.models import ContestStatus, ContestRuleType
from judge.tasks import judge_task
from options.options import SysOptions
# from judge.dispatcher import JudgeDispatcher
from problem.models import Problem, ProblemRuleType
from utils.api import APIView, validate_serializer
from utils.cache import cache
from utils.captcha import Captcha
from utils.throttling import TokenBucket
from ..models import Submission
from ..serializers import (CreateSubmissionSerializer, SubmissionModelSerializer,
                           ShareSubmissionSerializer)
from ..serializers import SubmissionSafeModelSerializer, SubmissionListSerializer
from datetime import datetime

class SubmissionAPI(APIView):
    def throttling(self, request):
        # 使用 open_api 的请求暂不做限制
        auth_method = getattr(request, "auth_method", "")
        if auth_method == "api_key":
            return
        user_bucket = TokenBucket(key=str(request.user.id),
                                  redis_conn=cache, **SysOptions.throttling["user"])
        can_consume, wait = user_bucket.consume()
        if not can_consume:
            return "Please wait %d seconds" % (int(wait))

        # ip_bucket = TokenBucket(key=request.session["ip"],
        #                         redis_conn=cache, **SysOptions.throttling["ip"])
        # can_consume, wait = ip_bucket.consume()
        # if not can_consume:
        #     return "Captcha is required"

    @check_contest_permission(check_type="problems")
    def check_contest_permission(self, request):
        contest = self.contest
        if contest.status == ContestStatus.CONTEST_ENDED:
            return self.error("The contest have ended")
        if not request.user.is_contest_admin(contest):
            user_ip = ipaddress.ip_address(request.session.get("ip"))
            if contest.allowed_ip_ranges:
                if not any(user_ip in ipaddress.ip_network(cidr, strict=False) for cidr in contest.allowed_ip_ranges):
                    return self.error("Your IP is not allowed in this contest")

    @validate_serializer(CreateSubmissionSerializer)
    @login_required
    def post(self, request):
        data = request.data
        hide_id = False
        if data.get("contest_id"):
            error = self.check_contest_permission(request)
            if error:
                return error
            contest = self.contest
            if not contest.problem_details_permission(request.user):
                hide_id = True

        if data.get("captcha"):
            if not Captcha(request).check(data["captcha"]):
                return self.error("Invalid captcha")
        error = self.throttling(request)
        if error:
            return self.error(error)

        try:
            problem = Problem.objects.get(id=data["problem_id"], contest_id=data.get("contest_id"), visible=True)
        except Problem.DoesNotExist:
            return self.error("Problem not exist")
        if data["language"] not in problem.languages:
            return self.error(f"{data['language']} is now allowed in the problem")
        submission = Submission.objects.create(user_id=request.user.id,
                                               username=request.user.username,
                                               language=data["language"],
                                               code=data["code"],
                                               problem_id=problem.id,
                                               ip=request.session["ip"],
                                               contest_id=data.get("contest_id"))
        # use this for debug
        # JudgeDispatcher(submission.id, problem.id).judge()
        judge_task.send(submission.id, problem.id)
        if hide_id:
            return self.success()
        else:
            return self.success({"submission_id": submission.id})

    @login_required
    def get(self, request):
        submission_id = request.GET.get("id")
        if not submission_id:
            return self.error("Parameter id doesn't exist")
        try:
            submission = Submission.objects.select_related("problem").get(id=submission_id)
        except Submission.DoesNotExist:
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user):
            return self.error("No permission for this submission")

        if submission.problem.rule_type == ProblemRuleType.OI or request.user.is_admin_role():
            submission_data = SubmissionModelSerializer(submission).data
        else:
            submission_data = SubmissionSafeModelSerializer(submission).data
        # 是否有权限取消共享
        submission_data["can_unshare"] = submission.check_user_permission(request.user, check_share=False)
        return self.success(submission_data)

    @validate_serializer(ShareSubmissionSerializer)
    @login_required
    def put(self, request):
        """
        share submission
        """
        try:
            submission = Submission.objects.select_related("problem").get(id=request.data["id"])
        except Submission.DoesNotExist:
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user, check_share=False):
            return self.error("No permission to share the submission")
        if submission.contest and submission.contest.status == ContestStatus.CONTEST_UNDERWAY:
            return self.error("Can not share submission now")
        # submission.shared = request.data["shared"]
        # submission.save(update_fields=["shared"])
        return self.error("No permission to share the submission")


from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils.dateparse import parse_datetime
from datetime import datetime

class SubmissionListAPI(APIView):
    def get(self, request):
        # Validate required parameters
        if not request.GET.get("limit"):
            return self.error("Limit is needed")
        if request.GET.get("contest_id"):
            return self.error("Parameter error")

        # Initial queryset with necessary filters
        submissions = Submission.objects.filter(contest_id__isnull=True).select_related("problem__created_by")
        problem_id = request.GET.get("problem_id")
        myself = request.GET.get("myself")
        result = request.GET.get("result")
        username = request.GET.get("username")

        
        spj_code = None
        rank_type = "time"
        
        if problem_id:
            try:
                spj_code = Problem.objects.get(_id=problem_id).spj_code
                if spj_code:
                    try:
                        spj_config_data = json.loads(spj_code)
                        rank_type = spj_config_data.get("rank_type", "time")
                    except:
                        pass
            except Problem.DoesNotExist:
                return self.error("Problem doesn't exist")
            
        if rank_type != "time" and rank_type != "memory":
            rank_type = "time"
            
        
        # Filter by problem_id if provided
        if problem_id:
            try:
                problem = Problem.objects.get(_id=problem_id, contest_id__isnull=True, visible=True)
            except Problem.DoesNotExist:
                return self.error("Problem doesn't exist")
            submissions = submissions.filter(problem=problem)
        else:
            # Without problem_id, filter by result BEFORE ranking has been assigned
            if result:
                submissions = submissions.filter(result=result)
                
            # Without problem_id, filter by user/username BEFORE ranking
            if (myself and myself == "1") or not SysOptions.submission_list_show_all:
                submissions = submissions.filter(user_id=request.user.id)
            elif username:
                submissions = submissions.filter(username__icontains=username)
        
        # Serialize all relevant submissions
        serialized_submissions = SubmissionListSerializer(submissions, many=True, user=request.user).data

        # If problem_id is provided, select the best submission per user
        if problem_id:
            
            
            if rank_type == "memory":
                # Sort all submissions globally
                sorted_submissions = sorted(
                    serialized_submissions,
                    key=lambda x: (
                        x["result"] == -3,  # Move -3 result to the end (lowest priority)
                        x["result"] != 0,  # Primary sort: result (0 first)
                        x.get("statistic_info", {}).get("memory_cost", float('inf'))  # Secondary sort: memory_cost
                    )
                )
            else:
                # Sort all submissions globally
                sorted_submissions = sorted(
                    serialized_submissions,
                    key=lambda x: (
                        x["result"] == -3,  # Move -3 result to the end (lowest priority)
                        x["result"] != 0,  # Primary sort: result (0 first)
                        x.get("statistic_info", {}).get("time_cost", float('inf'))  # Secondary sort: time_cost
                    )
                )
            
            
            # Select the best submission per user
            user_best_result = {}
            for item in sorted_submissions:
                username_item = item["username"]


                if username_item not in user_best_result:
                    user_best_result[username_item] = item
                else:
                    # Don't replace a valid submission with an expired one (result == -3)
                    if item["result"] == -3 and user_best_result[username_item]["result"] != -3:
                        continue
                    
                    # Only replace if the current best result is not accepted (result != 0)
                    if user_best_result[username_item]["result"] != 0:
                        try:
                            current_create_time = parse_datetime(item.get("create_time", ""))
                            best_create_time = parse_datetime(user_best_result[username_item].get("create_time", "1970-01-01T00:00:00"))
                        except (ValueError, TypeError):
                            current_create_time = datetime.min
                            best_create_time = datetime.min
                        if current_create_time > best_create_time:
                            user_best_result[username_item] = item
    
            # Convert to list and sort again to ensure proper ordering
            filtered_results = list(user_best_result.values())
            if rank_type == "memory":
                filtered_sorted_results = sorted(
                    filtered_results,
                    key=lambda x: (
                        x["result"] == -3,  # Move -3 result to the end (lowest priority)
                        x["result"] != 0,  # Primary sort: result (0 first)
                        x.get("statistic_info", {}).get("memory_cost", float('inf'))  # Secondary sort: memory_cost
                    )
                )
            else:
                filtered_sorted_results = sorted(
                    filtered_results,
                    key=lambda x: (
                        x["result"] == -3,  # Move -3 result to the end (lowest priority)
                        x["result"] != 0,  # Primary sort: result (0 first)
                        x.get("statistic_info", {}).get("time_cost", float('inf'))  # Secondary sort: time_cost
                    )
                )
    
            # Assign ranks
            for index, result_item in enumerate(filtered_sorted_results):
                result_item["rank"] = index + 1
    
            results = filtered_sorted_results
            
            # When problem_id is provided, filter by user/username and result AFTER ranking
            if result:
                results = [submission for submission in results if submission["result"] == int(result)]
                
            if (myself and myself == "1") or not SysOptions.submission_list_show_all:
                results = [submission for submission in results if submission["user_id"] == request.user.id]
            elif username:
                results = [submission for submission in results if username.lower() in submission["username"].lower()]
        else:
            # Assign ranks to all submissions first
            for index, result_item in enumerate(serialized_submissions):
                result_item["rank"] = index + 1
    
            results = serialized_submissions
            
        # Update total count after all filtering
        total = len(results)
        
        # Implement pagination on the filtered list
        limit = int(request.GET.get("limit"))
        page = request.GET.get("page", 1)
        paginator = Paginator(results, limit)
        try:
            paginated_results = paginator.page(page)
        except PageNotAnInteger:
            paginated_results = paginator.page(1)
        except EmptyPage:
            paginated_results = paginator.page(paginator.num_pages)
    
        # Prepare the final response data
        response_data = {
            "results": paginated_results.object_list,
            "total": total
        }
    
        return self.success(response_data)

class ContestSubmissionListAPI(APIView):
    @check_contest_permission(check_type="submissions")
    def get(self, request):
        if not request.GET.get("limit"):
            return self.error("Limit is needed")

        contest = self.contest
        submissions = Submission.objects.filter(contest_id=contest.id).select_related("problem__created_by")
        problem_id = request.GET.get("problem_id")
        myself = request.GET.get("myself")
        result = request.GET.get("result")
        username = request.GET.get("username")
        if problem_id:
            try:
                problem = Problem.objects.get(_id=problem_id, contest_id=contest.id, visible=True)
            except Problem.DoesNotExist:
                return self.error("Problem doesn't exist")
            submissions = submissions.filter(problem=problem)

        if myself and myself == "1":
            submissions = submissions.filter(user_id=request.user.id)
        elif username:
            submissions = submissions.filter(username__icontains=username)
        if result:
            submissions = submissions.filter(result=result)

        # filter the test submissions submitted before contest start
        if contest.status != ContestStatus.CONTEST_NOT_START:
            submissions = submissions.filter(create_time__gte=contest.start_time)

        # 封榜的时候只能看到自己的提交
        if contest.rule_type == ContestRuleType.ACM:
            if not contest.real_time_rank and not request.user.is_contest_admin(contest):
                submissions = submissions.filter(user_id=request.user.id)

        data = self.paginate_data(request, submissions)
        data["results"] = SubmissionListSerializer(data["results"], many=True, user=request.user).data
        return self.success(data)


class SubmissionExistsAPI(APIView):
    def get(self, request):
        if not request.GET.get("problem_id"):
            return self.error("Parameter error, problem_id is required")
        return self.success(request.user.is_authenticated and
                            Submission.objects.filter(problem_id=request.GET["problem_id"],
                                                      user_id=request.user.id).exists())
