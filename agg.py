"""
聚合K线，生成相应周期
"""
import time, datetime
import pandas as pd
import pickle
import os, multiprocessing
from dbHandle import dbHandle
from module_mylog import gLogger

class AggregateTickData(object):

    def __init__(self, dfInfo, date, aucTime):
        self.timeFilePath = os.getcwd() + '/' + 'timeSeriesFile/'
        self.barDict = {}
        self.splitDict = {}
        self.dfInfo = dfInfo
        self.timePoint = date
        self.cycle = [1, 5, 15, 30, 60]
        self.AucTime = aucTime
        self.initStart()

    def initStart(self):
        p = multiprocessing.Pool(5)
        manager = multiprocessing.Manager()
        work_queue = manager.Queue()
        done_queue = manager.Queue()
        lock = manager.Lock()

        self.db = dbHandle(lock)
        db = self.db.get_db("localhost", 27017, 'WIND_TICK_DB')
        names = self.db.get_all_colls(db)
        for i in names:
            Symbol = "".join([a for a in i if a.isalpha()]).lower()
            df = pd.DataFrame.from_records(list(self.db.get_specificDayItems(db, i, self.timePoint)))
            if df.empty:
                continue
            v = (Symbol, df, i)
            work_queue.put(v)
            while(work_queue.full()):
                gLogger.critical("work queue is fill, waiting......")
                time.sleep(1)
            if not work_queue.empty():
                p.apply_async(self.onto, args=(work_queue, done_queue, lock,))
                work_queue.put('STOP')

        p.close()
        p.join()

        done_queue.put('STOP')

        for status in iter(done_queue.get_nowait, 'STOP'):
            gLogger.warning(status)

    def onto(self, work_queue, done_queue, lock):
        for v in iter(work_queue.get_nowait, 'STOP'):
            try:
                Symbol = v[0]
                df = v[1]
                vtSymbol = v[2]
                gLogger.info('Run task %s (%s)...' % (vtSymbol, os.getpid()))
                self.getTimeList(self.cycle, Symbol, lock)
                self.genKData(vtSymbol, df)
                done_queue.put("%s process has done!" %vtSymbol)
                time.sleep(1)
            except Exception as e:
                done_queue.put("failed on process with %s!" %e)
        return True

    def getTimeList(self, cycle, Symbol, lock):
        try:
            gLogger.info("start getTimeList")
            if not os.path.exists(self.timeFilePath):
                os.makedirs(self.timeFilePath)
            self.genTimeList(Symbol, cycle)
            self.saveTimeList(Symbol, lock)
        except Exception as e:
            gLogger.exception("Exception : %s" %e)
            return False

    def saveTimeList(self, symbol, lock):
        lock.acquire()
        with open(self.timeFilePath + 'timeSeries_' + symbol + '.pkl', 'wb') as handle:
            pickle.dump(self.splitDict[symbol], handle, protocol=pickle.HIGHEST_PROTOCOL)
        lock.release()

    def genTimeList(self, symbol, cycle):
        try:
            tempDict = {}
            self.splitDict[symbol] = {}
            for c in cycle:
                gLogger.info("start genTimeList, cycle = %d" %c)
                tempDict[c] = []
                self.splitDict[symbol][c] = []
                tp = self.dfInfo.loc[symbol]["CurrPeriod"]
                time1 = [t for i in tp.split(',') for t in i.split('-')]
                for i in zip(*([iter(time1)] * 2)):
                    start = str(i[0]).strip()
                    end = str(i[1]).strip()
                    AucTime = [datetime.datetime.strptime(a, "%H:%M") for a in self.AucTime]
                    if datetime.datetime.strptime(start, "%H:%M") in AucTime:
                        start1 = datetime.datetime.strptime(start, "%H:%M") + datetime.timedelta(minutes=1)
                        start = start1.strftime("%H:%M")
                    else:
                        while([60 if datetime.datetime.strptime(start, "%H:%M").minute == 0 else datetime.datetime.strptime(start, "%H:%M").minute][0]%int(c) != 0):
                            if datetime.datetime.strptime(start, "%H:%M") > datetime.datetime.strptime(end, "%H:%M"):
                                break
                            start1 = datetime.datetime.strptime(start, "%H:%M") + datetime.timedelta(minutes=10)
                            start = start1.strftime("%H:%M")
                    tempList = pd.date_range(start, end, freq=(str(c) + 'min')).time.tolist()
                    tempDict[c].extend(tempList)

                tempDict[c].extend(pd.date_range(end, end, freq='1min').time.tolist())
                lst = list(set(tempDict[c]))
                lst.sort()
                self.splitDict[symbol][c] = lst
        except Exception as e:
            gLogger.exception("Exception : %s" %e)
            return False

    def genKData(self, vtSymbol, df_data):
        try:
            if not df_data.empty:
                cycle = self.cycle[1:]
                self.gen1minKData(vtSymbol, df_data)
                self.genOtherKData(vtSymbol, cycle)
                self.gen1DayKData(vtSymbol)
            else:
                gLogger.exception("df data is empty!")
        except Exception as e:
            return False

    def gen1minKData(self, vtSymbol, df_data):
        symbol = "".join([a for a in vtSymbol if a.isalpha()]).lower()
        try:
            gLogger.info("start gen1minKData , vtSymbol is %s" %vtSymbol)
            c = 1
            self.barDict[vtSymbol] = {}
            self.barDict[vtSymbol][c] = []
            df_data["structTime"] = df_data["time"].map(lambda x:datetime.datetime.strptime(x, "%H%M%S%f"))
            for i in zip(*[iter(self.splitDict[symbol][c][i:]) for i in range(2)]):
                start = datetime.datetime.strptime(str(i[0]).strip(), '%H:%M:%S')
                end = datetime.datetime.strptime(str(i[1]).strip(), '%H:%M:%S')
                if (start - datetime.timedelta(minutes=1)).strftime('%H:%M')in self.AucTime:
                    start = start - datetime.timedelta(minutes=1)
                p1 = df_data["structTime"] >= start
                p2 = df_data["structTime"] < end
                dfTemp = df_data.loc[p1 & p2]
                if len(dfTemp) > 1:
                    self.barDict[vtSymbol][c].append(self.aggMethod(dfTemp, c, str(i[0]).strip()))
            dbNew = self.db.get_db("localhost", 27017, 'WIND_1_MIN_DB')
            self.db.insert2db(dbNew, vtSymbol, self.barDict[vtSymbol][c])
        except Exception as e:
            gLogger.exception("Exception : %s" %e)
            return False

    def genOtherKData(self, vtSymbol, cycle):
        symbol = "".join([a for a in vtSymbol if a.isalpha()]).lower()
        for c in cycle:
            try:
                gLogger.info("start genOtherKData cycle = %d" %c)
                self.barDict[vtSymbol][c] = []
                for i in zip(*[iter(self.splitDict[symbol][c][i:]) for i in range(2)]):
                    start1 = time.strptime(str(i[0]).strip(), '%H:%M:%S')
                    end1 = time.strptime(str(i[1]).strip(), '%H:%M:%S')
                    fun = self.func1(start1)
                    selectItems = fun(end1)
                    items = list(map(selectItems, self.barDict[vtSymbol][1]))
                    items = list(filter(lambda x:x is not None, items))
                    dfTemp = pd.DataFrame(items)
                    if len(dfTemp) > 1:
                        self.barDict[vtSymbol][c].append(self.aggMethod(dfTemp, c, str(i[0]).strip()))
                dbNew = self.db.get_db("localhost", 27017, 'WIND_' + str(c) + '_MIN_DB')
                self.db.insert2db(dbNew, vtSymbol, self.barDict[vtSymbol][c])
            except Exception as e:
                gLogger.exception("Exception : %s" %e)
                return False

    def gen1DayKData(self, vtSymbol):
        try:
            gLogger.info("start gen1DayKData , vtSymbol = %s" %vtSymbol)
            c = '1Day'
            self.barDict[vtSymbol][c] = []
            items = self.barDict[vtSymbol][1]
            dfTemp = pd.DataFrame(items)
            if not dfTemp.empty:
                self.barDict[vtSymbol][c].append(self.aggMethod(dfTemp, c, "00:00"))
                dbNew = self.db.get_db("localhost", 27017, 'WIND_' + str(c) + '_MIN_DB')
                self.db.insert2db(dbNew, vtSymbol, self.barDict[vtSymbol][c])
        except Exception as e:
            gLogger.exception("Exception : %s" %e)
            return False

    def func1(self, s1):
        def func2(e1):
            def func3(x):
                ti = time.strptime(x["time"].strip(), '%H%M%S%f')
                if ti >= s1 and ti < e1:
                    return x
            return func3
        return func2

    def aggMethod(self, dfTemp, cflag, startTime):
        try:
            tempBar = {}
            st = dfTemp.iloc[0]["date"] + ' ' + startTime
            if cflag == 1:
                tempBar["vtSymbol"] = dfTemp.iloc[0]["vtSymbol"]
                tempBar["symbol"] = dfTemp.iloc[0]["symbol"]
                tempBar["date"] = dfTemp.iloc[0]["date"]
                tempBar["time"] = dfTemp.iloc[0]["time"]
                tempBar["openInterest"] = float(dfTemp.iloc[-1]["openInterest"])
                tempBar["volume"] = float(dfTemp["lastVolume"].sum())
                tempBar["turnover"] = float(dfTemp["lastTurnover"].sum())
                tempBar["high"] = float(max(dfTemp["lastPrice"]))
                tempBar["low"] = float(min(dfTemp["lastPrice"]))
                tempBar["open"] = float(dfTemp.iloc[0]["lastPrice"])
                tempBar["close"] = float(dfTemp.iloc[-1]["lastPrice"])
                tempBar["datetime"] = datetime.datetime.strptime(st, "%Y%m%d %H:%M:%S%f")
                return tempBar
            else:
                tempBar["vtSymbol"] = dfTemp.iloc[0]["vtSymbol"]
                tempBar["symbol"] = dfTemp.iloc[0]["symbol"]
                tempBar["date"] = dfTemp.iloc[0]["date"]
                tempBar["time"] = dfTemp.iloc[0]["time"]
                tempBar["openInterest"] = float(dfTemp.iloc[-1]["openInterest"])
                tempBar["volume"] = float(dfTemp["volume"].sum())
                tempBar["turnover"] = float(dfTemp["turnover"].sum())
                tempBar["high"] = float(max(dfTemp["high"]))
                tempBar["low"] = float(min(dfTemp["low"]))
                tempBar["open"] = float(dfTemp.iloc[0]["open"])
                tempBar["close"] = float(dfTemp.iloc[-1]["close"])
                if cflag == '1Day':
                    tempBar["datetime"] = datetime.datetime.strptime(tempBar["date"], "%Y%m%d")
                else:
                    tempBar["datetime"] = datetime.datetime.strptime(st, "%Y%m%d %H:%M:%S%f")
                return tempBar
        except Exception as e:
            gLogger.exception("Exception when exec aggMethod e:%s" %e)
            return False

