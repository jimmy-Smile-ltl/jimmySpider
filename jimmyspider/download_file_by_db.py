import concurrent
import datetime
import os
import concurrent.futures
import time

import pymongo

from jimmyspider.config import get_config
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.cache import Cache
from jimmyspider.file import FileDownloader
from typing import Optional , Literal

from jimmyspider.log_print import LogPrint
from jimmyspider.proxy_clash import ClashManager

class download_file_by_db:
    def __init__(self, db_name = "jimmy",
                 table_name = "",
                 file_field = "pdf_url",
                 sort_field = "_id",
                 sort_way = pymongo.ASCENDING,
                 batch_size = 100,
                 curl = True,
                 test_url = True,
                 max_workers = 20,
                 multi_way : Literal["thread","async"] = "thread",
                 requests_kwargs = {},
                 use_clash = False,
                 use_clash_pool = False,
                 flush_request_kwargs = None,
                 ):
        if not db_name or not table_name:
            print(f"请提供 db_name 和 table_name 参数 当前参数 db_name : {db_name} , table_name : {table_name}")
            raise ValueError("db_name 和 table_name 不能为空")
        self.db_name = db_name
        self.table_name = table_name
        self.file_path = os.path.join(get_config().DATA_DIR, table_name)
        self.file_field = file_field
        self.test_url = test_url
        if not os.path.exists(self.file_path):
            os.makedirs(self.file_path, exist_ok=True)
        if not os.path.exists(self.file_path + "/files"):
            os.makedirs(self.file_path + "/files", exist_ok=True)
        self.filter_condition = {
            "$and": [
                {
                    "$or": [
                        {"downloaded": {"$exists": False}},
                        {"downloaded": {"$ne": True}}
                    ]
                },
                {
                    f"{file_field}": {"$exists": True, "$nin": [None, ""]}
                }
            ]
        }
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.curl = curl
        self.file_downer = FileDownloader(pro_name= self.table_name,
                                          file_url_field = self.file_field,
                                          default_type=".pdf",
                                          max_workers = self.max_workers,
                                          test_url=self.test_url,
                                          curl=self.curl,
                                          use_clash_pool =use_clash_pool,
                                          **requests_kwargs)
        self.log_print = LogPrint(name=f"download_file_by_db_{table_name}",)
        self.sort_field = sort_field
        self.sort_way = sort_way
        self.log_last_id = Cache(f"{table_name}_download_file_by_db_last_id")
        self.db_manager = HandleMongoDB(table_name=table_name)
        self.file_field = file_field
        self.sort_field = sort_field
        self.sort_way =sort_way
        self.multi_way =multi_way

        self.use_clash = use_clash
        if self.use_clash and isinstance(self.use_clash, dict):
            self.clash_manager = ClashManager(self.use_clash)
        self.flush_request_kwargs = flush_request_kwargs

    def flush_state(self):
        # 是否使用 clash 切换节点
            # 完成 一个batch 就轮换 不一定是节点坏了
        if self.use_clash:
            print("正在刷新状态... 切换节点")
            self.clash_manager.switch_to_healthy_node(record_now = False)
        elif self.flush_request_kwargs:
            requests_kwargs = self.flush_request_kwargs()
            self.file_downer = FileDownloader(pro_name=self.table_name,
                                              file_url_field=self.file_field,
                                              default_type=".pdf",
                                              max_workers=self.max_workers,
                                              test_url=self.test_url,
                                              curl=self.curl,
                                              **requests_kwargs)
        else:
            self.log_print.print(f"flush_state 方法 被调用 ，但是不清楚做什么 默认等待30 分钟")
            time.sleep(30  * 60)



    def extract_one_batch(self, docs:list[dict],file_field:str ="pdf_url"):
        id_urls =  []
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_docs = {}
            for doc in docs:
                try:
                    future = executor.submit(self.extract_one_doc, doc,file_field)
                    future_docs[future] = doc.get("_id")
                except Exception as e:
                    self.log_print.print(f"提取文档 {doc.get('_id', '未知ID')} 时发生错误: {e}")
            for future in concurrent.futures.as_completed(future_docs):
                result = future.result()
                id_urls.append(result)
            return id_urls
                
        
    def extract_one_doc(self, doc , file_field):
        id_url = {
            "_id" :  doc.get("_id") ,
            file_field : doc.get(file_field),
        }
        return id_url
        
    def add_downloaded_field(self, doc):
        file_path = doc.get("file_path", None)
        doc["update_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if file_path:
            doc["downloaded"] = True
        else:
            doc["downloaded"] = False
    
    def add_download_fields(self, docs:list[dict]):
        for doc in docs:
            self.add_downloaded_field(doc)


    def download_one_batch(self, doc_batch):
        for retry in range(3):
            id_urls = self.extract_one_batch(doc_batch, self.file_field)
            if self.multi_way == "thread":
                result_list = self.file_downer.start_thread(id_urls=id_urls, default_type=".pdf")
            else:
                result_list = self.file_downer.start_async(id_urls=id_urls, default_type=".pdf")
            # 至少一个 下载完成
            if any(result.get("file_path", False) for result in result_list):
                self.add_download_fields(result_list)
                return result_list
            else:
                self.flush_state()


    def checkpoint_callback(self,id_str: str):
        self.log_last_id.record_string(id_str)

    def handle_one_row(self, doc):
        id_url = self.extract_one_doc(doc, self.file_field)
        result = self.file_downer._handle_one_file_speed(id_url=id_url)
        # 不是代理 是 clash  不能这么疯狂
        # if self.use_clash and not self.test_url:
        #     time.sleep(1)
        if result:
            id_url["downloaded"] = True
            id_url["file_path"] = result
            return id_url
        else:
            return None
    def run(self,batch=True):
        if batch:
            last_id = self.log_last_id.get_string(default="")
            self.db_manager.update_batch_in_bulk(filter=self.filter_condition,
                                            batch_size=self.batch_size,
                                            update_func=self.download_one_batch,
                                            resume_from_id=last_id,
                                            checkpoint_callback=self.checkpoint_callback,
                                            how = "all",
                                            sort_field = self.sort_field,
                                            sort_way = self.sort_way,
                                            logger =self.log_print

                                            )
        else:
            last_id = self.log_last_id.get_string(default="")
            self.db_manager.update_batch_in_bulk_loop(
                filter=self.filter_condition,
                batch_size=self.batch_size,
                update_func=self.handle_one_row,
                resume_from_id=last_id,
                checkpoint_callback=self.checkpoint_callback,
                sort_field=self.sort_field,
                sort_way=self.sort_way,
                logger=self.log_print,
                max_workers=self.max_workers,
                file_field = self.file_field,
                flush_state=self.flush_state
                                                 )


# if __name__ == '__main__':
#     table_name = "jstage"
#     file_downer = download_file_by_db(table_name=table_name,file_field ="pdf_url")
#     file_downer.run()
#
       
