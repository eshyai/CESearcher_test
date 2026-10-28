import { AuthGuard } from '@/components/auth-guard'
import { BaseLayout } from '@/layout/base'
import NotFound from '@/pages/404'
import LoginPage from '@/pages/auth/login'
import Chat from '@/pages/chat'
import NewChat from '@/pages/chat/newchat'
import Index from '@/pages/index'
import KnowledgePage from '@/pages/knowledge'
import DatabasePage from '@/pages/database'
import LibraryPage from '@/pages/library'
import NewsPage from '@/pages/news'
import TopicsPage from '@/pages/topics'
import {
  Navigate,
  Outlet,
  RouteObject,
  createBrowserRouter,
} from 'react-router-dom'

export type IRouteObject = {
  children?: IRouteObject[]
  name?: string
  auth?: boolean
  pure?: boolean
  meta?: any
} & Omit<RouteObject, 'children'>

export const routes: IRouteObject[] = [
  {
    path: '/',
    Component: Index,
  },
  {
    path: '/chat',
    children: [
      {
        path: '',
        Component: NewChat,
      },
      {
        path: ':id',
        Component: Chat,
      },
    ],
  },
  {
    path: '/knowledge',
    Component: KnowledgePage,
  },
  {
    path: '/database',
    Component: DatabasePage,
  },
  {
    path: '/news',
    Component: NewsPage,
  },
  {
    path: '/library',
    Component: LibraryPage,
  },
  {
    path: '/topics',
    Component: TopicsPage,
  },
  // 已下线页面：旧书签 / 旧链接直接回到资讯主入口，不抛 404
  {
    path: '/memory',
    element: <Navigate to="/news" replace />,
  },
  {
    path: '/bidding',
    element: <Navigate to="/news" replace />,
  },
  {
    path: '/404',
    Component: NotFound,
    pure: true,
  },
]

export const router = createBrowserRouter(
  [
    {
      path: '/login',
      element: <LoginPage />,
    },
    {
      path: '/',
      element: (
        <AuthGuard>
          <BaseLayout>
            <Outlet />
          </BaseLayout>
        </AuthGuard>
      ),
      children: routes,
    },
    {
      path: '*',
      element: <Navigate to="/404" />,
    },
  ] as RouteObject[],
  {
    basename: import.meta.env.BASE_URL,
  },
)
