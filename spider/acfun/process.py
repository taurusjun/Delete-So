#!/usr/bin/python
#coding=utf8

from base.Process import Process
from base.URL import urlWork
from base.Constants import *
from base.PO.ACcommentsInfoPO import *
from base.PO.ACcommentsPO import *
from multiprocessing.dummy import Pool as ThreadPool 
import os
import time
import json
import datetime
import sys
reload(sys)
sys.setdefaultencoding('utf8')
import socket
import urllib
import urllib2
import logging
import re

class process(Process, urlWork):
    '''
ACFUN的入口函数在这里。
    '''
    logging.basicConfig(level=logging.WARNING,
        format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
        datefmt='%a, %d %b %Y %H:%M:%S',
        # filename='/var/log/acmore.log',
        filename='run/log/acmore.log',
        filemode='a')
        
    def work(self, ac_comments):
        #数据全在内存里，自然要多一点保护
        try:
            #从acfun首页获取源代码用于解析热门投稿
            self.ac_comments = ac_comments
            url_data = self.sendGet(ACFUN)
            
            #从HTML中得到我们所需要的数据，格式为[[u'/v/ac1873087', u'title="\u5168\u660e\u661f\u70ed\u5531happy"']...]
            parse_data = self.get_parse_data(url_data)
            
            #从每篇投稿中获取基本的信息
            analyse_data = self.parse(parse_data)
            
            #使用map进行多线程分析每篇投稿的评论
            # pool = ThreadPool(1)
            pool = ThreadPool(16)
            insert_data = pool.map(self.analyse, analyse_data) #这里的输出为ACComments结构体的数组
            pool.close()
            pool.join()
            
            #将数据从[[], [], []]转换为[,,,]
            insert_data = self.clear_insert_data(insert_data)
            
            #发送评论
            for node in insert_data:
                self.ac_comments.insert(node)
            
            #数据清理
            self.clearDB()
            logging.warn('lru length: ' + str(self.ac_comments.lru.size))
                   
        except Exception as e:
            logging.error(e.message)
                
    def get_parse_data(self, urlContent):
        parse_data = []
        
        pattern_1 = re.compile(r'''
            <a.*?</a>     
            ''', re.VERBOSE)
        pattern_2 = re.compile(r'''
            /a/ac[0-9]* | /v/ac[0-9]* | /v/ab[0-9]* |     #从<a xxxxx </a>中拿到投稿url和title就OK
            title=".*?"
            ''', re.VERBOSE)
        
        #按照<a xxxxx </a>解析
        src = pattern_1.findall(urlContent)
        
        #从<a xxxxx </a>中拿到投稿url和title就OK
        tmp = []
        for s in src:
            tmp = pattern_2.findall(s)
            if len(tmp) == 2:
                parse_data.append(tmp)
                
        return parse_data
        
    '''
          数据结构约定：
          rows[row[投稿URL, 投稿类型, 投稿标题, UP主, 投稿时间], row, row...]
    '''
    def parse(self, src):
        max_id = 0 #max_id这个字段用来查询更多的投稿
        now = 0
        rows = []#front_urlData
        for data in src:
            try:
                row = ACcommentsInfoPO() #保存一篇投稿抓取的内容
                
                #获取投稿类型
                if data[0][0:5] == '/v/ac':
                    row.set_id(data[0][5:])
                    row.set_type('视频')
                elif data[0][0:5] == '/a/ac':
                    row.set_id(data[0][5:])
                    row.set_type('文章')
                elif data[0][0:5] == '/v/ab':
                    #番剧的id和其他不一样，加负号以示区别
                    row.set_id('-' + data[0][5:])
                    row.set_type('番剧')
                else:
                    continue
                
                #获取acid和url
                row.set_url(ACFUN + data[0])
                
                #max_id这个字段用来查询更多的投稿，比如我从首页获取的最大投稿是ac190000，那么一会我会多抓去ac188900到ac190000的评论信息
                if max_id < int(data[0][5:]):
                    max_id = int(data[0][5:])

                self.parseTitleText(row,data[1]);

            except Exception:
                continue
                
            rows.append(row)
        
        #开始随机抓取评论
        self.create_more(rows, max_id)
        #投稿信息单独放一张表
        self.ac_comments.db_proc.ACCommentsInfo.insert(rows)
           
        return list(set(rows))

    '''
          标题文本解析
          row是要返回的，data是输入的标题文本
    '''
    def parseTitleText(self,row, txt):
        # 2017／09／07 更新：分析了目前title的格式，基本是是 XXXXX &#13 YYYYY &#13 加时间戳
        # 因此更新了解析逻辑

        # 以下3个字段是需要更新到row里面的默认值
        title = "No title"
        author = "unknown"
        post_time = "1888-06-17 01:02:03"

        # 先过滤掉前面几个字
        wholeTxt = txt[7:]

        spliter0 = '&#13;'
        spliterUp = 'UP:'

        split0Idx = wholeTxt.find(spliter0)
        if split0Idx !=-1: # 找到第一个标记'&#13;"的起始位置, 如果未找到，说明是单标题
            # 找到第二个标记'&#13;"的起始位置
            split1Idx = wholeTxt.find(spliter0, split0Idx+len(spliter0))
            title = wholeTxt[:split0Idx].strip();
            if split1Idx != -1: # 如果找到两个标记，则取中间这一段字符串进行处理
                # 取出中间一段string
                str2 = wholeTxt[split0Idx+1:split1Idx]
                # 找UP标记
                upIndx = str2.find(spliterUp);
                if  upIndx != -1: # 找到UP标记，则取UP主名, 没找到UP标记，说明是其他内容
                    author = str2[upIndx+len(spliterUp):]
                # 取剩余的字符串
                str3 = wholeTxt[split1Idx+len(spliter0):]
            else:
                str3 = wholeTxt[split0Idx+len(spliter0):]

            # 标题中的时间格式是 2017-09-07 18:38:26，使用正则表达式来抽取
            # dtPattern = re.compile(r'''
            #     ([1-2][0-9][0-9][0-9]-[0-1][1-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9])
            #     ''', re.VERBOSE)
            match =re.search(r'([1-2][0-9][0-9][0-9]-[0-1][1-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9])',str3)
            post_time = match.group(1)
        else:
            title = wholeTxt[:len(wholeTxt) - 1].strip();

        titleSymble = u'标题: '
        titleIdx = title.find(titleSymble)
        if titleIdx != -1:
            title = title[titleIdx+len(titleSymble):]
        row.set_title(title)
        row.set_up(author)
        row.set_post_time(post_time)

    '''
          标题文本解析
          row是要返回的，data是输入的标题文本
    '''
    def oldParseTitleText(self,row, data):
        # 到了解析title的时候了，A站的title写的很不规范，以下是我们需要分析的几种情况
        # 情况1 什么都有，开工的时候要注意标题后面的两种冒号： title="标题: 抗战烽火孕育新中国国歌 《义勇军进行曲》唱响世界80年&#13;UP主: 亡是公&#13;发布于 2015-05-24 20:22:26 / 点击数: 2602 / 评论数: 96"
        # 情况2 没有UP主，就一个更新时间，这种数据常见于番剧： title="标题：【四月】幻界战线 &#13;更新至：第8集 &#13;更新于：2015年05月24日"
        # 情况3 这下更牛B，连更新时间都不需要了，一般是推荐 ： title="全明星热唱happy"
        # 情况4 还有这种有标题但没有UP主的                  ： title="标题: 我爱你 中国（A站爱国兔子合集）"

        # 先过滤掉前面几个字
        data[1] = data[1][7:]

        # 先排除情况3
        now = data[1].find('标题')
        if now == -1:
            row.set_title(data[1][:len(data[1]) - 1].strip())
            # 然后开始疯狂捏造数据
            row.set_up("UP主不详")
            row.set_post_time("1992-06-17 01:02:03")
        else:
            data[1] = data[1][3:]
            # 然后排除情况2
            now = data[1].find('更新于')
            if now != -1:
                now = data[1].find('&#13;')
                # 获取title
                row.set_title(data[1][:now].strip())
                # 然后也开始疯狂捏造数据
                row.set_up("UP主不详")
                row.set_post_time("1992-06-17 01:02:03")
            else:
                now = data[1].find('&#13;')
                if now != -1:
                    # 接着处理情况1
                    # 获取title
                    row.set_title(data[1][:now].strip())
                    data[1] = data[1][now + 1:]

                    # 获取UP主
                    now = data[1].find('&#13;')
                    row.set_up(data[1][9:now])
                    data[1] = data[1][now + 1:]

                    # 获取投稿时间
                    now = data[1].find(' / ')
                    row.set_post_time(data[1][8:now])
                else:
                    # 最后是情况4
                    row.set_title(data[1][:len(data[1]) - 1].strip())
                    # 然后开始疯狂捏造数据
                    row.set_up("UP主不详")
                    row.set_post_time("1992-06-17 01:02:03")

    '''
          数据结构约定：
          rows[row[评论cid, 评论内容, 评论人用户名, 引用评论cid, 该评论楼层数, 投稿URL, 删除标志, 司机标志, 时间戳], row, row...]
    '''
    def analyse(self, src):
        #初始化一个row，不然极端情况下程序会崩溃
        row = [] #保存一篇投稿的评论
        strACid = int(src.get_id())
        acid = strACid
        #番剧的id小于0
        if acid > 0:
            url = "http://www.acfun.tv/comment_list_json.aspx?contentId=" + str(acid) + "&currentPage=1"
        else:
            url = 'http://www.acfun.tv/comment/bangumi/web/list?bangumiId=' + str(-acid) + '&pageNo=1'

        jsonContent = self.sendGet(url)
        if not self.checkURL(jsonContent):
            logging.warning("connect acfun comments fail")
            return 
        
        try:
            j_obj = json.loads(jsonContent)
        except Exception:
            logging.warning("get acfun comments fail")
            return 
        
        #番剧的id小于0
        try:
            # if acid > 0:
            #     json_data = j_obj["commentContentArr"]
            # else:
            #     json_data = j_obj['data']["commentContentArr"]
            # 2017／09／07 目前统一采用了如下结构
            json_data = j_obj['data']["commentContentArr"]
        except:
            logging.error("commentContentArr is not exist")
            return
        
        #偶尔会出现找不到commentContentArr的情况
        try:
            #开始解析json评论
            for m, n in enumerate(json_data):
                comment = ACcommentsPO() #保存一条评论的内容
                comment.set_acid(int(acid)) #抓取投稿编号            
                comment.set_cid(int(json_data[n]["cid"])) #抓取评论cid
                comment.set_content(json_data[n]["content"]) #抓取评论内容
                comment.set_user_name(json_data[n]["userName"]) #抓取评论人用户名
                comment.set_layer(int(json_data[n]["count"])) #抓取该评论楼层数
                userID = int(json_data[n]["userID"]) #抓取评论人用户ID
                
                #司机判断
                self.checkSIJI(comment)
                
                #删除判断
                self.checkDelete(comment, userID)
                
                #时间戳
                comment.set_check_time(str(datetime.datetime.now()))
                
                #数据下盘时间需要商量一下
                row.append(comment)
                
                #不能浪费太多时间在拥有超大评论量的投稿上
                if m > 3000:
                    logging.error("over 3000, drop it.")
                    break
                
        except BaseException, e:
            logging.error(e.message)
            return 
            
        return row
    
    def checkURL(self, urlContent):
        if urlContent == URL_EXCEPTION \
        or urlContent == URL_FUALT:
            return False
        else:
            return True
    #unfinished
    def checkSIJI(self, comment):
        if comment.get_content().find(u"佛曰：") > -1 \
        or comment.get_content().find(u"如是我闻：") > -1 \
        or comment.get_content().find(u"*：") > -1:
            comment.set_siji(1)
        elif comment.get_content().find(u"ed2k://") > -1:
            linkUrl = "ed2k:" + comment.get_content()[comment.get_content().find(u"ed2k://"):]
            encodedContent = comment.get_content().replace(self.encodeFoyu(linkUrl),linkUrl,1)
            comment.set_content(encodedContent)
            comment.set_siji(1)
        elif comment.get_content().find(u"magnet:?") > -1:
            linkUrl = "magnet:?" + comment.get_content()[comment.get_content().find(u"magnet:?"):]
            encodedContent = comment.get_content().replace(self.encodeFoyu(linkUrl),linkUrl,1)
            comment.set_content(encodedContent)
            comment.set_siji(1)
        else:
            comment.set_siji(0)
    
    #含有ed2k和磁力链的评论发送至与佛论禅转化成佛语
    def encodeFoyu(self, text):
        url = "http://www.keyfc.net/bbs/tools/tudou.aspx"
        values = {"orignalMsg": text,
                 "action": "Encode"}
        data = urllib.urlencode(values)
        request = urllib2.Request(url,data)
        try:
            response = urllib2.urlopen(request)
            responseText = response.read()
        #之前那种错误处理不行
        except Exception as e:
            logging.error("foyu encode error, " + str(e))
        encodedFoyu = responseText[responseText.find("佛曰") : responseText.find("]]></Message>")]
        response.close()
        return encodedFoyu
            
    def checkDelete(self, comment, userID):
        #判断是否为已删除，为4则表示删除
        # if userID == 4:
        if userID == -1:
            comment.set_delete(1)
        else:
            comment.set_delete(0)
        
    def clearDB(self):
        logging.debug("clear...")
        if os.path.getsize("run/log/acmore.log") > 1048576:
            os.system("> run/log/acmore.log")

    #用来构造更多投稿的字段
    def create_more(self, rows, ac_id):
        for i in range(1, 750):
            row = ACcommentsInfoPO()
            now_id = str(ac_id - i)
            row.set_id(now_id)
            row.set_url(ACFUN_URL + now_id)
            row.set_type("类型未知")
            row.set_title("标题不详")
            row.set_up("UP主不详")
            row.set_post_time("1992-06-17 01:02:03")
            rows.append(row)
            
    #将以投稿形式的数组转换为以每条评论分开的数组
    def clear_insert_data(self, datas):
        result = []
        for data in datas:
            if not data:
                continue
            
            for comment in data:
                if not comment:
                    continue
                
                result.append(comment)
                
        return result
