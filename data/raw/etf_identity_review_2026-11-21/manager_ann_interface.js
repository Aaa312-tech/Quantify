
var currentPage = "";
var columnId = "267";
var pageNo = 1;
var isLoading = false; // 添加加载状态标记
var mobilePageNo = 0; // 添加移动端页码计数

// 产品资料概要 / 招募说明书：中文公告对应英文版 PDF（按 articleFile 中的 UUID 匹配）
var FANN_EN_VERSION_MAP = {
    "4ce7cd3e-f476-4756-942f-ad343347a826":
        "https://www.fsfund.com/static//webimages/upload2012/2026/08/25/23e2e95f-a4a1-471c-ba44-cd29268c68fc/Hwabao%20ChiNext%20AI%20ETF_KFS_EN_clean(chopped).pdf",
    "ebcc91d7-b98f-4a1f-8975-2bee8b084034":
        "https://www.fsfund.com/static//webimages/upload2012/2026/08/25/5796fa64-6f73-46d5-8d57-d045dd9d85b5/%E5%8D%8E%E5%AE%9D%E5%88%9B%E4%B8%9A%E6%9D%BF%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%E4%BA%A4%E6%98%93%E5%9E%8B%E5%BC%80%E6%94%BE%E5%BC%8F%E6%8C%87%E6%95%B0%E8%AF%81%E5%88%B8%E6%8A%95%E8%B5%84%E5%9F%BA%E9%87%91%E6%8B%9B%E5%8B%9F%E8%AF%B4%E6%98%8E%E4%B9%A6%EF%BC%88%E6%9B%B4%E6%96%B0%EF%BC%89_EN_20260629%20(chopped).pdf"
};

function fann_getEnVersionUrl(articleFile) {
    if (!articleFile) {
        return "";
    }
    var key = String(articleFile).trim();
    for (var uuid in FANN_EN_VERSION_MAP) {
        if (FANN_EN_VERSION_MAP.hasOwnProperty(uuid) && key.indexOf(uuid) > -1) {
            return FANN_EN_VERSION_MAP[uuid];
        }
    }
    return "";
}

function fann_buildEnVersionLink(articleFile) {
    var enUrl = fann_getEnVersionUrl(articleFile);
    if (!enUrl) {
        return "";
    }
    return "<span class='fann-en-version' data-en-url='" + enUrl + "' style='cursor:pointer;color:#1a66b5;'>（Click here for English version）</span>";
}

// 英文版链接点击：阻止冒泡，避免触发外层中文公告链接
$(document).off("click.fannEnVersion", ".fann-en-version").on("click.fannEnVersion", ".fann-en-version", function (e) {
    e.preventDefault();
    e.stopPropagation();
    var enUrl = $(this).attr("data-en-url");
    if (enUrl) {
        window.open(enUrl, "_blank");
    }
    return false;
});

// 初始化函数，由外部按需调用
function fann_init() {
    fann_initEvent();
    // 根据设备类型决定调用哪个函数，避免重复调用
    var isMobile = typeof mobileFlag !== 'undefined' ? mobileFlag : (window.innerWidth < 1024);
    if (isMobile) {
        // 移动端只调用移动端查询
        fann_queryArticleListH5();
    } else {
        // PC端只调用PC端查询
        fann_queryArticleList();
    }
}

// 移除自动初始化，改为按需加载
// $(function () {
//     fann_init();
// });


// 绑定事件
function fann_initEvent1() {
    $("#pageNum a").on("click", function () {
        if ($(this).hasClass("active")) {
            return false;
        }
        $(this).addClass("active").siblings("li").removeClass("active");
        pageNo = parseInt($(this).attr("data-pagenum"));
        fann_queryArticleList();
    });
}

    // 初始化滚动事件（仅移动端）
    $(document).ready(function() {
        const mbAnnList = document.querySelector('#mbAnnList');
        // 只有当元素存在时才绑定滚动事件（移动端）
        if (mbAnnList) {
            mbAnnList.addEventListener('scroll', function() {
                // 检查是否滚动到底部
                if (mbAnnList.scrollHeight - mbAnnList.scrollTop <= mbAnnList.clientHeight + 1) {
                    // 触发滚动到底部事件
                    fann_queryArticleListH5();
                }
            });
        }
    });

//绑定事件
function fann_initEvent() {
    // 移动端 公告tab 切换
    $("#mb_ann p").on("click", function () {
        $(this).addClass("mb_act").siblings("p").removeClass("mb_act");
        var index = $(this).index();
        var value = $(this).attr("data-value")
        columnId = value
        mobilePageNo = 0; // 重置移动端页码计数
        $("#mbAnnList").html("");
        fann_queryArticleListH5();
    });

    // 先解绑之前的事件，避免重复绑定
    $("#ann_tab li").off("click").on("click", function (e) {
        e.preventDefault(); // 阻止默认行为
        e.stopPropagation(); // 阻止事件冒泡
        columnId = $(this).attr("data-value")
        $(this).addClass("act").siblings("li").removeClass("act");
        var index = $(this).index();
        var value = $(this).attr("data-value")
        $('#annList' + value).show().siblings("ul").hide();
        pageNo=1;
        fann_queryArticleList();
    });
    $("#start_page").on("click", function () {
        pageNo = 1;
        fann_queryArticleList();
    });
    $("#end_page").on("click", function () {
        pageNo = pTotal;
        fann_queryArticleList();
    });
    $("#go_page").on("click", function () {
        var page = parseInt($("#page_input").val());
        if (!page) {
            return false;
        }
        pageNo = page < 1 ? 1 : (page > pTotal ? pTotal : page);
        $("#page_input").val(pageNo);
        fann_queryArticleList();
    });
    $("#pre_page").on("click", function () {
        if (pageNo == 1) {
            return false;
        }
        pageNo--;
        fann_queryArticleList();
    });
    $("#next_page").on("click", function () {
        if (pageNo == pTotal) {
            return false;
        }
        pageNo++;
        fann_queryArticleList();
    });

}

function fann_queryArticleList() {
    const webArticleInfo = {
        fundCode: fundCode,
        columnId: columnId,
        pageSize:10,
        offset:(pageNo-1)*10
    };
    $.ajax({
        type: "POST",
        url: basePath + "/queryController/queryArticles",
        data: JSON.stringify(webArticleInfo),
        contentType: 'application/json',
        async: false,
        success: function (data) {
            if (data.code == "0000") {
                var result = data.data;
                var pageSums=0;
                var html = "";
                if (result.length > 0) {
                    for (var i = 0; i < result.length; i++) {
                        html += "<li>";
                        if (result[i].articleType == "1") {
                            html += "<p><a href='" + result[i].articleUrl + "' title='" + result[i].title + "' target='_blank' >"+result[i].title+"</a></p>";
                        }else if (result[i].articleType == "2") {
                            html += "<p><a href='" + result[i].articleFile + "' title='" + result[i].title + "' target='_blank'>" + result[i].title + "</a>" + fann_buildEnVersionLink(result[i].articleFile) + "</p>";
                        }else {
                            html += "<p><a href='/finaSchool/news.shtml?queryFlag=1&id=" + result[i].id + "' title='" + result[i].title + "' target='_blank'>"+result[i].title+"</a></p>";
                        }
                        html += " <span class='time'>"+result[i].publishDate1+"</span>";
                        html += "</li>";
                        pageSums = result[i].pageSums;
                    }
                    if(columnId=='267'){
                        $("#annList267").html(html);
                    }
                    if(columnId=='281'){
                        $("#annList281").html(html);
                    }
                    if(columnId=='603'){
                        $("#annList603").html(html);
                    }
                    $('#pageSums').html(pageSums);
                    var pageTotal = Math.ceil(pageSums / 10);
                    pTotal = pageTotal;
                    var pageHtml = "";
                    var pageEnd = 10;
                    var pageIndex = 1;
                    if (pageTotal <= 10) {
                        pageEnd = pageTotal
                    } else if (pageNo > 5 && pageTotal > 10) {
                        pageEnd = pageNo + ((pageTotal - pageNo) > 5 ? 5 : (pageTotal - pageNo));
                        pageIndex = pageNo - 4;
                    }
                    for (var i = pageIndex; i <= pageEnd; i++) {
                        if (i == pageNo) {
                            pageHtml += '<a href="javascript:void(0);" class="page_num active" data-pagenum="' + i + '"> ' + i + ' </a>\n';
                        } else {
                            pageHtml += '<a href="javascript:void(0);" class="page_num" data-pagenum="' + i + '"> ' + i + ' </a>\n';
                        }
                    }
                    $("#pageNum").html(pageHtml);
                    $("#pege_templatenumber").show();
                    fann_initEvent1();
                } else {
                    if(columnId=='267'){
                        $("#annList267").html("<p>暂无数据</p>");
                    }
                    if(columnId=='281'){
                        $("#annList281").html("<p>暂无数据</p>");
                    }
                    if(columnId=='603'){
                        $("#annList603").html("<p>暂无数据</p>");
                    }
                    $("#pege_templatenumber").hide();
                }
            } else {
                if(columnId=='267'){
                    $("#annList267").html("<p>暂无数据</p>");
                }
                if(columnId=='281'){
                    $("#annList281").html("<p>暂无数据</p>");
                }
                if(columnId=='603'){
                    $("#annList603").html("<p>暂无数据</p>");``
                }
                $("#pege_templatenumber").hide();
            }
        },
        error: function (data) {

        }

    });

}

function fann_queryArticleListH5() {
    if (isLoading) return; // 如果正在加载或已经是最后一页，则不执行
    mobilePageNo++;

    isLoading = true;

    const webArticleInfo = {
        fundCode: fundCode,
        columnId: columnId,
        pageSize:20,
        offset:(mobilePageNo-1)*20
    };
    $.ajax({
        type: "POST",
        url: basePath + "/queryController/queryArticles",
        data: JSON.stringify(webArticleInfo),
        contentType: 'application/json',
        async: false,
        success: function (data) {
            if (data.code == "0000") {
                var result = data.data;
                var html = "";
                if (result.length > 0) {
                    for (var i = 0; i < result.length; i++) {
                        html += '<li class="flex_between mb_ann_p">';
                        if (result[i].articleType == "1") {
                            html += '<p style="width: 80%;"><a href="'+result[i].articleUrl+'" target="_blank" class="mb_t_gray">'+result[i].title+'</a></p>';
                        }else if (result[i].articleType == "2") {
                            html += '<p style="width: 80%;"><a href="'+result[i].articleFile+'" target="_blank" class="mb_t_gray">' + result[i].title + '</a>' + fann_buildEnVersionLink(result[i].articleFile) + '</p>';
                        }else {
                            html += '<p style="width: 80%;"><a href="/finaSchool/news.shtml?queryFlag=1&id=' + result[i].id + '" target="_blank" class="mb_t_gray">'+result[i].title+'</a></p>';
                        }
                        html += '<span class="time">'+result[i].publishDate1+'</span>';
                        html += '</li>';
                    }
                    $('#mbAnnList').append(html);
                }
            }
            if (mobilePageNo == 1 && data.code != "0000") {
                $("#mbAnnList").html("<p>暂无数据</p>");
            }
            isLoading = false; // 重置加载状态
        },
        error: function (data) {
            isLoading = false; // 重置加载状态
            $("#mbAnnList").html("<p>暂无数据</p>");
        }

    });

}


$("#keyword").click(function() {
    fann_queryArticleList()
});


