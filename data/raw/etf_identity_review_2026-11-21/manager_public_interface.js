// basePath = localObj.protocol + "//" + localObj.host +contextPath;
// basePath = 'https://hjdev.fsfund.com/fund-counter-gateway/batch/webzk';
var basePath = "";
// 网点当前签名密钥（由服务端每10分钟轮换，前端每5分钟刷新）
var webSignKey = "";
var signKeyUrl = "";

$(function(){
    basePath = $("#basePath").val();
    // 密钥接口：/v2/webzk -> /v2/account/account/common/getSignKey
    if (basePath) {
        signKeyUrl = basePath.replace(/\/webzk\/?$/, '/account') + '/account/common/getSignKey';
        refreshSignKey(true);
        // 每5分钟刷新一次签名密钥
        setInterval(function() {
            refreshSignKey(false);
        }, 5 * 60 * 1000);
    }
    
    // 初始化Ajax拦截器
    initAjaxInterceptor();
});

/**
 * 从服务端获取当前签名密钥
 * @param {boolean} sync 是否同步请求（首次加载时同步，确保后续请求有密钥）
 */
function refreshSignKey(sync) {
    if (!signKeyUrl) {
        return;
    }
    $.ajax({
        url: signKeyUrl,
        type: 'POST',
        contentType: 'application/json;charset=UTF-8',
        data: JSON.stringify({netNo: 'web'}),
        async: !sync,
        success: function(res) {
            if (res && res.code === '0000' && res.data) {
                webSignKey = res.data;
            } else {
                console.error('获取签名密钥失败:', res);
            }
        },
        error: function(xhr) {
            console.error('获取签名密钥异常:', xhr.status);
        }
    });
}

/**
 * 初始化Ajax拦截器，对所有Ajax请求进行签名
 */
function initAjaxInterceptor() {
    // 保存原始的ajax方法
    var originalAjax = $.ajax;
    
    // 重写$.ajax方法
    $.ajax = function(options) {
        // 如果有data参数，进行签名处理
        if (options.data) {
            options.data = addSignature(options.data, options.contentType);
        }
        
        // 添加请求头
        if (!options.headers) {
            options.headers = {};
        }
        options.headers['netNo'] = 'web';
        
        // 调用原始的ajax方法
        return originalAjax.call(this, options);
    };
}

/**
 * 为请求数据添加签名
 * @param {*} data 请求数据
 * @param {string} contentType 内容类型
 * @returns {*} 添加签名后的数据
 */
function addSignature(data, contentType) {
    var newParams = {};
    
    // 如果是JSON字符串，先解析
    if (typeof data === 'string' && contentType && contentType.indexOf('application/json') > -1) {
        try {
            newParams = JSON.parse(data);
        } catch (e) {
            console.error('JSON解析失败:', e);
            return data;
        }
    } else if (typeof data === 'object') {
        newParams = data;
    } else {
        // 如果是表单数据字符串，解析为对象
        if (typeof data === 'string') {
            var pairs = data.split('&');
            for (var i = 0; i < pairs.length; i++) {
                var pair = pairs[i].split('=');
                if (pair.length === 2) {
                    newParams[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1]);
                }
            }
        } else {
            return data;
        }
    }
    newParams.netNo = 'web';
    newParams.timestamp = new Date().getTime();
    
    // 处理参数，过滤空值
    var _newParams = {};
    for (var key in newParams) {
        if (newParams.hasOwnProperty(key)) {
            var value = newParams[key];
            if (value !== null && value !== '' && value !== 'null' && 
                value !== undefined && value.toString().length > 0) {
                
                // 如果是对象类型，转换为JSON字符串
                if (typeof value === 'object' && value !== null) {
                    var subMap = {};
                    for (var subKey in value) {
                        if (value.hasOwnProperty(subKey)) {
                            var subValue = value[subKey];
                            if (subValue !== null && subValue !== '' && subValue !== 'null' && 
                                subValue !== undefined && subValue.toString().length > 0) {
                                subMap[subKey] = subValue.toString();
                            }
                        }
                    }
                    _newParams[key] = JSON.stringify(subMap);
                } else {
                    _newParams[key] = value.toString();
                }
            }
        }
    }
    
    // 生成签名（密钥由服务端动态下发）
    var signStr = objKeySort(_newParams);
    signStr += 'key=' + webSignKey;
    
    var signData = generateMd5(signStr);
    _newParams['signature'] = signData;
    
    // 根据原始数据类型返回相应格式
    if (typeof data === 'string' && contentType && contentType.indexOf('application/json') > -1) {
        return JSON.stringify(_newParams);
    } else if (typeof data === 'object') {
        return _newParams;
    } else {
        // 转换为表单数据字符串
        var formData = [];
        for (var key in _newParams) {
            if (_newParams.hasOwnProperty(key)) {
                formData.push(encodeURIComponent(key) + '=' + encodeURIComponent(_newParams[key]));
            }
        }
        return formData.join('&');
    }
}

/**
 * 对象键排序，生成签名字符串
 * @param {Object} arys 参数对象
 * @returns {string} 排序后的参数字符串
 */
function objKeySort(arys) {
    var newkey = Object.keys(arys);
    
    // key排序 - 按字符编码排序
    newkey.sort(function(a, b) {
        var al = [];
        var bl = [];
        
        for (var i = 0; i < a.length; i++) {
            al.push(a.charCodeAt(i));
        }
        for (var i = 0; i < b.length; i++) {
            bl.push(b.charCodeAt(i));
        }
        
        for (var i = 0; i < al.length; i++) {
            if (bl.length <= i) return 1;
            if (al[i] > bl[i]) {
                return 1;
            } else if (al[i] < bl[i]) {
                return -1;
            } else {
                if (i === al.length - 1) {
                    return -1;
                }
            }
        }
        return 0;
    });
    
    var newObj = '';
    for (var i = 0; i < newkey.length; i++) {
        var key = newkey[i];
        var value = arys[key];
        
        // 空值，空字符串，不参与签名，signature字段不参与签名
        if (value !== null && value !== '' && key !== 'signature' && value !== 'null') {
            newObj += key + '=' + value + '&';
        }
    }
    
    return newObj;
}

/**
 * 生成MD5哈希值
 * @param {string} data 要加密的数据
 * @returns {string} MD5哈希值
 */
function generateMd5(data) {
    return CryptoJS.MD5(data).toString();
}

function numMulti(num1, num2, fixed = 2) {
    var baseNum = 0;
    try {
        baseNum += num1.toString().split(".")[1].length;
    } catch (e) {
    }
    try {
        baseNum += num2.toString().split(".")[1].length;
    } catch (e) {
    }
    return (Number(num1.toString().replace(".", "")) * Number(num2.toString().replace(".", "")) / Math.pow(10, baseNum)).toFixed(fixed);
}